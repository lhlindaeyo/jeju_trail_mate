"""Proactive notification engine (spec §11). Cheap deterministic rules run on each location
batch; no LLM call per GPS update. Safety messages are template-built from verified data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Notification, TrackingSession, User
from ..services import route_service, schedule_service, weather_service
from ..services.geo import KST, as_utc, haversine_m, hhmm_to_minutes, sunset_kst
from ..services.tracking_service import OFF_ROUTE_M, ROUTE_MATCH_ACCURACY_M, progress_summary, route_progress

EVENT_TYPES = ["waypoint_arrival", "off_route", "endpoint_near", "schedule_delay", "rest_reminder", "end_time_risk",
               "route_notice", "notice_unverified", "weather", "journal_prompt"]
OFF_ROUTE_SUSTAIN = timedelta(minutes=2)
ENDPOINT_NEAR_M = 150.0
RAIN_PROB_THRESHOLD = 60


@dataclass
class Event:
    event_type: str
    event_key: str
    severity: str  # safety | info
    title: str
    message: str
    actions: list[dict] = field(default_factory=list)
    source: dict | None = None
    dedupe_window_min: int | None = None  # None → once per session

    @property
    def is_safety_related(self) -> bool:
        return self.severity == "safety"


ACT_MAP = {"id": "open_map", "label": "지도 보기"}
ACT_REPLAN = {"id": "replan", "label": "일정 다시 계산"}
ACT_LATER = {"id": "later", "label": "나중에"}
ACT_SNOOZE = {"id": "snooze_today", "label": "오늘은 그만"}
ACT_NOTICE = {"id": "open_notice", "label": "공지 원문 열기"}
ACT_JOURNAL = {"id": "open_journal", "label": "기록하기"}
ACT_PHOTO = {"id": "open_journal", "label": "사진 남기기"}


def evaluate(db: Session, session: TrackingSession, user: User, now: datetime | None = None, *, weather: dict | None = None) -> list[Event]:
    now = now or datetime.now(timezone.utc)
    s = get_settings()
    prefs = user.preferences
    trip = session.trip
    local = now.astimezone(KST)
    sched = schedule_service.approved_for(db, trip, local.date())
    summary = progress_summary(session, sched, now)
    gps_ok = summary["gps_quality"] != "poor" and not summary["gps_stale"]
    events: list[Event] = []

    if session.status == "ended":
        events.append(Event("journal_prompt", "journal_prompt", "info", "오늘의 기록",
                            "오늘 기억하고 싶은 순간이 있나요? 한 줄이나 음성으로 남겨 보세요.", [ACT_JOURNAL, ACT_LATER]))
        return events
    if session.status != "active":
        return events

    route = session.route
    # 1) Official notices (safety) — and an honest warning when we have not checked recently.
    if route:
        for n in route_service.active_notices(db, route, now):
            kind = {"detour": "우회", "closure": "통제", "safety": "안전"}.get(n.notice_type, "안내")
            events.append(Event("route_notice", f"notice:{n.id}", "safety", f"[{kind} 공지] {route.name}",
                                f"{n.title}\n공식 공지를 확인하고 일정을 다시 검토하세요.", [ACT_NOTICE, ACT_REPLAN],
                                {"url": n.source_url, "verified_at": n.verified_at.isoformat() if n.verified_at else None}))
        if route_service.route_freshness(route, now)["notices_status"] != "checked":
            payload = route_service.notices_payload(db, route)
            events.append(Event("notice_unverified", f"notice_unverified:{route.id}", "info", "공지 최신 확인 필요",
                                payload["caveat"], [ACT_NOTICE, ACT_LATER], {"url": payload["official_url"]}))

        # 1b) Waypoint check-ins (routes with located waypoints) → offer a photo/diary entry.
        for v in sorted(session.visits, key=lambda v: as_utc(v.arrived_at)):
            w = v.waypoint
            if w.kind == "S":
                continue
            done = w.kind == "F"
            events.append(Event("waypoint_arrival", f"waypoint:{w.id}", "info",
                                "코스 완주 지점 도착" if done else f"경유지 도착 · {w.name}",
                                (f"{route.name}의 도착 지점이에요. 오늘 걸은 길을 기록해 볼까요?" if done
                                 else f"{w.name} 근처예요. 사진을 남기고 일기에 연결할까요?"),
                                [{**ACT_PHOTO, "waypoint_id": w.id}, ACT_LATER],
                                {"waypoint": w.name, "data": "하영올레 SHP (제주데이터허브)"}))

        # 1c) Possible deviation — only with good fixes, sustained, phrased as "위치 확인".
        if (route.has_geometry and session.off_route_since and session.off_route_m
                and session.off_route_m > OFF_ROUTE_M and (session.last_accuracy_m or 999) <= ROUTE_MATCH_ACCURACY_M
                and as_utc(session.last_point_at or now) - as_utc(session.off_route_since) >= OFF_ROUTE_SUSTAIN):
            events.append(Event("off_route", "off_route", "info", "위치 확인이 필요해요",
                                f"경로에서 약 {round(session.off_route_m)}m 떨어진 것 같아요. 건물·신호 대기로 생긴 오차일 수 있으니 "
                                "지도에서 위치를 확인해 주세요.", [ACT_MAP, ACT_LATER], dedupe_window_min=15))

        # 1d) Official Olle courses have no path data: confirm proximity to the end point if known.
        if not route.has_geometry and route.end_lat is not None and session.last_lat is not None and gps_ok:
            d = haversine_m(session.last_lat, session.last_lon, route.end_lat, route.end_lon)
            if d <= ENDPOINT_NEAR_M:
                events.append(Event("endpoint_near", f"endpoint:{route.id}", "info", "종점 근처",
                                    f"{route.end_point} 근처(약 {round(d)}m)예요. 기록을 남기고 세션을 마칠까요?",
                                    [ACT_JOURNAL, ACT_LATER]))

    # 2) Schedule delay — only when GPS is trustworthy (spec guardrail).
    exp, act = summary["expected_progress"], summary["actual_progress"]
    if gps_ok and exp is not None and act is not None and summary["active_minutes"] >= 20:
        if exp - act > s.delay_threshold_ratio:
            events.append(Event("schedule_delay", "schedule_delay", "info", "계획보다 늦어지고 있어요",
                                f"예상 진행률 {exp:.0%}, 실제 약 {act:.0%}입니다. 도착 시각을 다시 계산하거나 종료 지점을 조정해 보세요.",
                                [ACT_REPLAN, ACT_MAP, ACT_LATER], dedupe_window_min=s.notification_window_minutes))

    # 3) Rest reminder — user-configured interval, no medical claims.
    every = prefs.rest_every_minutes or 0
    if every >= 30 and summary["active_minutes"] >= every:
        bucket = int(summary["active_minutes"] // every)
        events.append(Event("rest_reminder", f"rest:{bucket}", "info", "잠시 쉬어 갈까요?",
                            f"{summary['active_minutes']}분째 걷고 있어요. 설정하신 대로 잠깐 쉬어 가는 건 어떨까요?", [ACT_LATER, ACT_SNOOZE]))

    # 4) End time / sunset risk (safety).
    prog = route_progress(session)
    if route and prog["done_m"] is not None:
        eta = schedule_service.estimate_arrival(route_distance_km=route.distance_km, walked_m=prog["done_m"], pace_kmh=prefs.pace_kmh,
                                                rest_every_minutes=prefs.rest_every_minutes, now=local)
        sunset = sunset_kst(local.date())
        end_target = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=hhmm_to_minutes(prefs.walk_end_time))
        limit_dt = min(end_target, sunset)
        if eta["remaining_km"] > 0 and datetime.fromisoformat(eta["eta"]) > limit_dt:
            certainty = "" if gps_ok else " (GPS 신호가 약해 추정이 부정확할 수 있습니다)"
            events.append(Event("end_time_risk", "end_time_risk", "safety", "종료 목표 시각 이후 도착 예상",
                                f"예상 도착 {eta['eta_hhmm']}, 일몰 {sunset.strftime('%H:%M')}, 목표 종료 {prefs.walk_end_time}입니다{certainty}. "
                                "가까운 마을이나 큰길에서 오늘 걷기를 마치고 귀환 수단을 확인하세요.",
                                [ACT_MAP, ACT_REPLAN], {"calc": eta["assumptions"]}, dedupe_window_min=60))

    # 5) Weather — only from official forecast data; skip silently when unavailable.
    if weather is None and session.last_lat is not None:
        weather = weather_service.get_weather(session.last_lat, session.last_lon, hours=3)
    if weather and weather.get("status") == "ok":
        for f in weather.get("forecast", [])[:3]:
            try:
                pop = int(f.get("precip_prob_pct") or 0)
            except ValueError:
                pop = 0
            if pop >= RAIN_PROB_THRESHOLD:
                events.append(Event("weather", f"rain:{f['time'][:13]}", "info", "비 소식",
                                    f"{f['time'][11:16]}경 강수확률 {pop}% 예보입니다. (출처: {weather['source']}, 발표 {weather['base_time']})",
                                    [ACT_MAP, ACT_LATER], {"url": weather.get("source_url"), "base_time": weather.get("base_time")}))
                break
    return events


def _type_enabled(user: User, event_type: str) -> bool:
    ns = user.preferences.notification_settings or {}
    if ns.get("enabled") is False:
        return False
    return (ns.get("types") or {}).get(event_type, True)


def _snoozed_today(db: Session, user: User, event_type: str, now: datetime) -> bool:
    local_midnight = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    q = select(Notification.id).where(
        Notification.user_id == user.id, Notification.event_type == event_type,
        Notification.action_taken == "snooze_today", Notification.created_at >= local_midnight.astimezone(timezone.utc),
    )
    return db.scalar(q.limit(1)) is not None


def _duplicate(db: Session, session_id: str, ev: Event, now: datetime) -> bool:
    q = select(Notification).where(Notification.session_id == session_id, Notification.event_key == ev.event_key)
    if ev.dedupe_window_min is not None:
        q = q.where(Notification.created_at >= now - timedelta(minutes=ev.dedupe_window_min))
    return db.scalar(q.limit(1)) is not None


def deliver(db: Session, session: TrackingSession, user: User, events: list[Event], now: datetime | None = None) -> list[Notification]:
    now = now or datetime.now(timezone.utc)
    out = []
    for ev in events:
        if not _type_enabled(user, ev.event_type):
            continue
        if _duplicate(db, session.id, ev, now):
            continue
        if not ev.is_safety_related and _snoozed_today(db, user, ev.event_type, now):
            continue
        n = Notification(user_id=user.id, session_id=session.id, event_key=ev.event_key, event_type=ev.event_type,
                         severity=ev.severity, title=ev.title, message=ev.message, actions=ev.actions, source=ev.source,
                         created_at=now)
        db.add(n)
        out.append(n)
    db.commit()
    return out


def on_location_batch(db: Session, session: TrackingSession, user: User, now: datetime | None = None) -> list[Notification]:
    if session.tracking_mode == "gps" and not user.preferences.consent_location:
        return []
    return deliver(db, session, user, evaluate(db, session, user, now), now)


def notification_to_dict(n: Notification) -> dict:
    return {
        "id": n.id, "event_type": n.event_type, "severity": n.severity, "title": n.title, "message": n.message,
        "actions": n.actions or [], "source": n.source, "created_at": as_utc(n.created_at).isoformat(),
        "action_taken": n.action_taken,
    }
