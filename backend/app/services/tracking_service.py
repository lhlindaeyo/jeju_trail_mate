"""Trekking sessions and GPS ingest (spec F-04). Raw points are validated, filtered and
summarised; only the summary (never the raw log) is shared with the LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GpsPoint, TrackingSession
from .geo import KST, haversine_m, hhmm_to_minutes, valid_coordinate

MAX_WALKING_SPEED_MS = 4.0  # ~14 km/h — faster segments are treated as GPS jumps / vehicle
RAW_POINT_RETENTION_DAYS = 30


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pause(s: TrackingSession, now: datetime | None = None) -> None:
    if s.status == "active":
        s.status, s.paused_at = "paused", now or datetime.now(timezone.utc)


def resume(s: TrackingSession, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if s.status == "paused" and s.paused_at:
        s.paused_seconds += int((now - _aware(s.paused_at)).total_seconds())
        s.status, s.paused_at = "active", None


def end(s: TrackingSession, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if s.status == "paused":
        resume(s, now)
    s.status, s.ended_at = "ended", now


def active_minutes(s: TrackingSession, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    stop = _aware(s.ended_at) or (_aware(s.paused_at) if s.status == "paused" else None) or now
    return max((stop - _aware(s.started_at)).total_seconds() - s.paused_seconds, 0) / 60.0


def ingest_points(db: Session, s: TrackingSession, points: list[dict]) -> dict:
    """points: [{timestamp, lat, lon, accuracy_m}] — returns counts of accepted/rejected."""
    if s.status != "active":
        return {"accepted": 0, "rejected": len(points), "reason": f"session is {s.status}"}
    min_acc = get_settings().min_gps_accuracy_m
    accepted = rejected = 0
    for p in sorted(points, key=lambda x: x["timestamp"]):
        ts = _aware(p["timestamp"])
        lat, lon, acc = p["lat"], p["lon"], p.get("accuracy_m")
        if not valid_coordinate(lat, lon) or ts < _aware(s.started_at) - timedelta(minutes=1):
            rejected += 1
            continue
        db.add(GpsPoint(session_id=s.id, timestamp=ts, lat=lat, lon=lon, accuracy_m=acc, source=p.get("source", "device")))
        accepted += 1
        good = acc is None or acc <= min_acc
        if good and s.last_lat is not None and s.last_point_at is not None:
            seg = haversine_m(s.last_lat, s.last_lon, lat, lon)
            dt = (ts - _aware(s.last_point_at)).total_seconds()
            # ignore jitter (< accuracy) and implausible jumps
            if dt > 0 and seg / dt <= MAX_WALKING_SPEED_MS and seg >= min(acc or 5.0, 15.0):
                s.distance_m += seg
        if good:
            s.last_lat, s.last_lon, s.last_point_at = lat, lon, ts
        s.last_accuracy_m = acc
    db.commit()
    return {"accepted": accepted, "rejected": rejected, "distance_m": round(s.distance_m, 1)}


def progress_summary(s: TrackingSession, schedule=None, now: datetime | None = None) -> dict:
    """Compact summary used by rules engine, UI and the AI context builder."""
    now = now or datetime.now(timezone.utc)
    walked = s.manual_distance_m if s.tracking_mode == "manual" and s.manual_distance_m is not None else s.distance_m
    route_km = s.route.distance_km if s.route else None
    actual_ratio = min(walked / (route_km * 1000), 1.0) if route_km else None
    expected_ratio = None
    if schedule and schedule.planned_start and schedule.planned_end:
        start = hhmm_to_minutes(schedule.planned_start)
        end_ = hhmm_to_minutes(schedule.planned_end)
        local = now.astimezone(KST)
        cur = local.hour * 60 + local.minute
        if end_ > start:
            expected_ratio = min(max((cur - start) / (end_ - start), 0.0), 1.0)
    acc = s.last_accuracy_m
    gps_quality = "unknown" if acc is None else ("good" if acc <= get_settings().min_gps_accuracy_m else "poor")
    stale = s.last_point_at is None or now - _aware(s.last_point_at) > timedelta(minutes=10)
    return {
        "session_id": s.id,
        "status": s.status,
        "tracking_mode": s.tracking_mode,
        "route": s.route.name if s.route else None,
        "route_distance_km": route_km,
        "walked_km": round(walked / 1000, 2),
        "active_minutes": round(active_minutes(s, now)),
        "actual_progress": round(actual_ratio, 3) if actual_ratio is not None else None,
        "expected_progress": round(expected_ratio, 3) if expected_ratio is not None else None,
        "gps_quality": gps_quality,
        "gps_stale": stale if s.tracking_mode == "gps" else False,
        "last_position": [round(s.last_lat, 4), round(s.last_lon, 4)] if s.last_lat is not None else None,
        "note": "진행률은 GPS 누적 거리를 코스 총거리로 나눈 추정치입니다(공식 경로 좌표 미사용).",
    }


def purge_old_points(db: Session, days: int = RAW_POINT_RETENTION_DAYS) -> int:
    """Minimum-retention policy for raw GPS (spec §09). Summaries on the session remain."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ended = select(TrackingSession.id).where(TrackingSession.ended_at.is_not(None), TrackingSession.ended_at < cutoff)
    res = db.execute(delete(GpsPoint).where(GpsPoint.session_id.in_(ended)))
    db.commit()
    return res.rowcount or 0
