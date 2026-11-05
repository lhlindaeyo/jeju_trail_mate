"""Itinerary planning (spec F-03). Pure code: the LLM never computes time or distance.

Approval model: every change creates a *new revision* of the affected dates. The approved
plan for a date is its highest approved revision; drafts never alter it until the user
approves (spec: '사용자 승인 전 확정 일정 변경 금지')."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

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


def estimate_arrival(*, route_distance_km: float | None, walked_m: float, pace_kmh: float,
                     rest_every_minutes: int, now: datetime | None = None) -> dict:
    now = now or now_kst()
    if route_distance_km is None:
        return {"status": "unavailable", "message": "코스 거리 데이터가 없어 도착 시각을 계산할 수 없습니다."}
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
        text = " ".join(r.tags or []) + (r.name or "")
        overlap = sum(1 for i in interests if i in text)
        return (-overlap, abs((r.distance_km or target) - target))

    return sorted(routes, key=score)


def _limit_min(prefs: UserPreferences, day: date) -> int:
    sunset = sunset_kst(day)
    return min(hhmm_to_minutes(prefs.walk_end_time), sunset.hour * 60 + sunset.minute - 30)  # 30-min margin


def _duration(routes: list[TrailRoute], prefs: UserPreferences) -> int | None:
    if any(r.distance_km is None for r in routes):
        return None
    return estimate_duration_min(sum(r.distance_km for r in routes), prefs.pace_kmh, prefs.rest_every_minutes)


def _fits(route: TrailRoute | list[TrailRoute], prefs: UserPreferences, start_hhmm: str, day: date) -> tuple[bool, int | None, str | None]:
    routes = route if isinstance(route, list) else [route]
    dur = _duration(routes, prefs)
    if dur is None:
        return False, None, None
    start = hhmm_to_minutes(start_hhmm)
    km = sum(r.distance_km for r in routes)
    ok = km <= prefs.daily_distance_target_km * 1.15 and start + dur <= _limit_min(prefs, day)
    return ok, dur, fmt_hhmm(start + dur)


def _describe(routes: list[TrailRoute]) -> str:
    if routes[0].trail_type == "hayoung_olle":
        names = " + ".join(f"{r.course_no}코스" for r in routes)
        return f"하영올레 {names} · 서귀포시청 출발·도착"
    r = routes[0]
    return f"{r.start_point} → {r.end_point}"


def _assign(row: DailySchedule, routes: list[TrailRoute], prefs: UserPreferences, start: str, day: date) -> None:
    ok, _dur, end = _fits(routes, prefs, start, day)
    row.route_id = routes[0].id
    row.extra_route_ids = [r.id for r in routes[1:]]
    row.planned_start = start
    row.planned_end = end
    row.note = _describe(routes)
    if end is None:
        row.note += " · 거리 데이터 적재 전이라 소요 시간을 계산하지 않았습니다"
        row.checklist = [*row.checklist, "하영올레 경로 데이터(SHP) 적재 후 소요 시간 다시 계산"]
    elif not ok:
        row.note += " · 하루 거리 또는 종료 시각(일몰 30분 전)을 넘을 수 있어요"


def generate_plan(db: Session, trip: Trip, prefs: UserPreferences, route_ids: list[str] | None = None,
                  rest_days: list[date] | None = None, trail_type: str = "jeju_olle") -> list[DailySchedule]:
    """Create a draft revision for every date of the trip.

    * Picked routes keep the user's order. Consecutive Hayoung courses are bundled into one day
      while they fit (they all start and end at Seogwipo City Hall).
    * Without picks, recommends courses of `trail_type`; for Jeju Olle it prefers the course that
      starts where the previous day ended (종점→시점 연결)."""
    rest_days = set(rest_days or [])
    explicit = bool(route_ids)
    if explicit:
        remaining = [r for rid in route_ids if (r := db.get(TrailRoute, rid))]
    else:
        remaining = _rank_routes(list_routes(db, trail_type=trail_type), prefs)
    drafts: list[DailySchedule] = []
    prev_end: str | None = None
    day = trip.start_date
    while day <= trip.end_date:
        row = DailySchedule(trip_id=trip.id, date=day, revision=_next_revision(db, trip, day), checklist=list(DEFAULT_CHECKLIST))
        start = prefs.walk_start_time
        if day in rest_days:
            row.note, row.checklist = "휴식일", []
        elif explicit and remaining:
            today = [remaining.pop(0)]
            while (today[0].trail_type == "hayoung_olle" and remaining and remaining[0].trail_type == "hayoung_olle"
                   and _fits(today + [remaining[0]], prefs, start, day)[0]):
                today.append(remaining.pop(0))
            _assign(row, today, prefs, start, day)
        elif not explicit:
            candidates = [r for r in remaining if _fits(r, prefs, start, day)[0]]
            if prev_end:
                linked = [r for r in candidates if r.start_point and r.start_point == prev_end]
                candidates = linked or candidates
            if candidates:
                r = candidates[0]
                remaining.remove(r)
                _assign(row, [r], prefs, start, day)
                prev_end = r.end_point
            else:
                row.note = "조건(하루 거리·종료 시각·일몰)에 맞는 코스가 없어 비워 두었습니다."
        else:
            row.note, row.checklist = "고른 코스를 모두 배정했어요. 자유 일정", []
        db.add(row)
        drafts.append(row)
        day += timedelta(days=1)
    db.commit()
    return drafts


def hayoung_bundles(db: Session, prefs: UserPreferences, day: date) -> list[dict]:
    """반나절(1) / 하루(1+2) / 여유(3코스 포함) suggestions with honest time estimates."""
    by_no = {r.course_no: r for r in list_routes(db, trail_type="hayoung_olle")}
    options = [("반나절", ["1"]), ("하루", ["1", "2"]), ("여유 일정", ["3"]), ("전 코스", ["1", "2", "3"])]
    out = []
    for label, nos in options:
        routes = [by_no[n] for n in nos if n in by_no]
        if len(routes) != len(nos):
            continue
        ok, dur, end = _fits(routes, prefs, prefs.walk_start_time, day)
        out.append({"label": label, "route_ids": [r.id for r in routes], "courses": nos,
                    "distance_km": round(sum(r.distance_km for r in routes), 1) if dur is not None else None,
                    "duration_min": dur, "fits_today": ok if dur is not None else None,
                    "note": None if dur is not None else "경로 데이터 적재 전이라 거리·시간을 계산할 수 없어요"})
    return out


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
        extra = [r for rid in ch.get("extra_route_ids") or [] if (r := db.get(TrailRoute, rid))]
        start = ch.get("planned_start") or prefs.walk_start_time
        if route:
            _assign(row, [route, *extra], prefs, start, day)
            if ch.get("note"):
                row.note = ch["note"]
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

    def brief(x):
        return {"id": x.id, "trail_type": x.trail_type, "course_no": x.course_no, "official_code": x.course_no,
                "name": x.name, "distance_km": x.distance_km, "start_point": x.start_point, "end_point": x.end_point}

    extras = [brief(x) for rid in (s.extra_route_ids or []) if (x := object_session(s).get(TrailRoute, rid))]
    return {
        "id": s.id,
        "date": s.date.isoformat(),
        "revision": s.revision,
        "route": brief(r) if r else None,
        "extra_routes": extras,
        "planned_start": s.planned_start,
        "planned_end": s.planned_end,
        "note": s.note,
        "checklist": s.checklist or [],
        "user_approved_at": s.user_approved_at.isoformat() if s.user_approved_at else None,
    }
