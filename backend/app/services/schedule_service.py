"""Itinerary planning (spec F-03). Pure code: the LLM never computes time or distance.

Approval model: every change creates a *new revision* of the affected dates. The approved
plan for a date is its highest approved revision; drafts never alter it until the user
approves (spec: '사용자 승인 전 확정 일정 변경 금지')."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DailySchedule, TrailRoute, Trip, UserPreferences
from .geo import fmt_hhmm, hhmm_to_minutes, now_kst, sunset_kst
from .route_service import list_routes

REST_MINUTES = 10
DEFAULT_CHECKLIST = [
    "시작 지점까지 이동 수단(버스·택시) 확인",
    "숙박 위치와 귀환 동선 확인",
    "출발 당일 제주올레 공식 사이트의 우회·통제 공지 확인",
]


def estimate_duration_min(distance_km: float, pace_kmh: float, rest_every_minutes: int) -> int:
    walking = distance_km / max(pace_kmh, 1.0) * 60
    rests = int(walking // max(rest_every_minutes, 30))
    return int(round(walking + rests * REST_MINUTES))


def estimate_arrival(*, route_distance_km: float, walked_m: float, pace_kmh: float,
                     rest_every_minutes: int, now: datetime | None = None) -> dict:
    now = now or now_kst()
    remaining_km = max(route_distance_km - walked_m / 1000.0, 0.0)
    remaining_min = estimate_duration_min(remaining_km, pace_kmh, rest_every_minutes)
    eta = now + timedelta(minutes=remaining_min)
    return {
        "remaining_km": round(remaining_km, 2),
        "remaining_min": remaining_min,
        "eta": eta.isoformat(),
        "eta_hhmm": eta.strftime("%H:%M"),
        "sunset_hhmm": sunset_kst(now.date()).strftime("%H:%M"),
        "assumptions": f"평균 {pace_kmh}km/h, {rest_every_minutes}분마다 {REST_MINUTES}분 휴식 가정. GPS 누적 거리 기준 추정치입니다.",
    }


def _next_revision(db: Session, trip: Trip, day: date) -> int:
    cur = db.scalar(select(func.max(DailySchedule.revision)).where(DailySchedule.trip_id == trip.id, DailySchedule.date == day))
    return (cur or 0) + 1


def _rank_routes(routes: list[TrailRoute], prefs: UserPreferences) -> list[TrailRoute]:
    interests = set(prefs.interests or [])
    target = prefs.daily_distance_target_km

    def score(r: TrailRoute):
        overlap = len(interests & set(r.tags or []))
        return (-overlap, abs(r.distance_km - target))

    return sorted(routes, key=score)


def _fits(route: TrailRoute, prefs: UserPreferences, start_hhmm: str, day: date) -> tuple[bool, int, str]:
    dur = estimate_duration_min(route.distance_km, prefs.pace_kmh, prefs.rest_every_minutes)
    start = hhmm_to_minutes(start_hhmm)
    end_limit = hhmm_to_minutes(prefs.walk_end_time)
    sunset = sunset_kst(day)
    sunset_min = sunset.hour * 60 + sunset.minute - 30  # keep a 30-min margin before sunset
    limit = min(end_limit, sunset_min)
    ok = route.distance_km <= prefs.daily_distance_target_km * 1.15 and start + dur <= limit
    return ok, dur, fmt_hhmm(start + dur)


def generate_plan(db: Session, trip: Trip, prefs: UserPreferences, route_ids: list[str] | None = None,
                  rest_days: list[date] | None = None) -> list[DailySchedule]:
    """Create a draft revision for every date of the trip."""
    rest_days = set(rest_days or [])
    if route_ids:
        pool = [r for rid in route_ids if (r := db.get(TrailRoute, rid))]
    else:
        pool = _rank_routes(list_routes(db), prefs)
    remaining = list(pool)
    drafts: list[DailySchedule] = []
    day = trip.start_date
    while day <= trip.end_date:
        row = DailySchedule(trip_id=trip.id, date=day, revision=_next_revision(db, trip, day), checklist=list(DEFAULT_CHECKLIST))
        if day in rest_days:
            row.note = "휴식일"
            row.checklist = []
        else:
            chosen = None
            for r in remaining:
                ok, _dur, end = _fits(r, prefs, prefs.walk_start_time, day)
                if ok:
                    chosen = (r, end)
                    break
            if chosen:
                r, end = chosen
                remaining.remove(r)
                row.route_id, row.planned_start, row.planned_end = r.id, prefs.walk_start_time, end
                row.note = f"{r.start_point} → {r.end_point}"
            else:
                row.note = "조건(하루 거리·종료 시각·일몰)에 맞는 코스가 없어 비워 두었습니다."
        db.add(row)
        drafts.append(row)
        day += timedelta(days=1)
    db.commit()
    return drafts


def propose_changes(db: Session, trip: Trip, prefs: UserPreferences, changes: list[dict]) -> list[DailySchedule]:
    """changes: [{date: 'YYYY-MM-DD', route_id: str|None, planned_start: 'HH:MM'|None, note: str|None}]
    Creates draft revisions for only the affected dates."""
    drafts = []
    for ch in changes:
        day = date.fromisoformat(ch["date"]) if isinstance(ch["date"], str) else ch["date"]
        if not (trip.start_date <= day <= trip.end_date):
            raise ValueError(f"{day} is outside the trip")
        row = DailySchedule(trip_id=trip.id, date=day, revision=_next_revision(db, trip, day), checklist=list(DEFAULT_CHECKLIST))
        route = db.get(TrailRoute, ch["route_id"]) if ch.get("route_id") else None
        start = ch.get("planned_start") or prefs.walk_start_time
        if route:
            _ok, _dur, end = _fits(route, prefs, start, day)
            row.route_id, row.planned_start, row.planned_end = route.id, start, end
            row.note = ch.get("note") or f"{route.start_point} → {route.end_point}"
        else:
            row.note = ch.get("note") or "휴식일"
            row.checklist = []
        db.add(row)
        drafts.append(row)
    db.commit()
    return drafts


def approve(db: Session, trip: Trip, dates: list[date] | None = None) -> list[DailySchedule]:
    """Approve the latest unapproved revision for each requested date (all dates if None)."""
    approved = []
    now = datetime.now(timezone.utc)
    for day, rows in _rows_by_date(db, trip).items():
        if dates and day not in dates:
            continue
        latest = rows[-1]
        if latest.user_approved_at is None:
            latest.user_approved_at = now
            approved.append(latest)
    db.commit()
    return approved


def _rows_by_date(db: Session, trip: Trip) -> dict[date, list[DailySchedule]]:
    rows = db.scalars(select(DailySchedule).where(DailySchedule.trip_id == trip.id).order_by(DailySchedule.date, DailySchedule.revision))
    out: dict[date, list[DailySchedule]] = {}
    for r in rows:
        out.setdefault(r.date, []).append(r)
    return out


def approved_for(db: Session, trip: Trip, day: date) -> DailySchedule | None:
    rows = _rows_by_date(db, trip).get(day, [])
    approved = [r for r in rows if r.user_approved_at]
    return approved[-1] if approved else None


def plan_view(db: Session, trip: Trip) -> list[dict]:
    view = []
    for day, rows in _rows_by_date(db, trip).items():
        approved = [r for r in rows if r.user_approved_at]
        latest = rows[-1]
        view.append({
            "date": day.isoformat(),
            "approved": schedule_to_dict(approved[-1]) if approved else None,
            "pending_draft": schedule_to_dict(latest) if latest.user_approved_at is None else None,
        })
    return view


def schedule_to_dict(s: DailySchedule) -> dict:
    r = s.route
    return {
        "id": s.id,
        "date": s.date.isoformat(),
        "revision": s.revision,
        "route": {"id": r.id, "official_code": r.official_code, "name": r.name, "distance_km": r.distance_km,
                  "difficulty": r.difficulty, "start_point": r.start_point, "end_point": r.end_point} if r else None,
        "planned_start": s.planned_start,
        "planned_end": s.planned_end,
        "note": s.note,
        "checklist": s.checklist or [],
        "user_approved_at": s.user_approved_at.isoformat() if s.user_approved_at else None,
    }
