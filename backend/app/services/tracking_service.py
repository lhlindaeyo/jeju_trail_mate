"""Trekking sessions and GPS ingest (spec F-04). Raw points are validated, filtered and
summarised; only the summary (never the raw log) is shared with the LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GpsPoint, TrackingSession, WaypointVisit
from .geo import KST, as_utc, haversine_m, hhmm_to_minutes, project_on_path, valid_coordinate

MAX_WALKING_SPEED_MS = 4.0  # ~14 km/h — faster segments are treated as GPS jumps / vehicle
RAW_POINT_RETENTION_DAYS = 30
ROUTE_MATCH_ACCURACY_M = 30.0  # fixes worse than this never move progress or trigger check-ins
OFF_ROUTE_M = 50.0  # distance from the path that counts as "possibly off route" (tune in field test)
WAYPOINT_RADIUS_M = 40.0  # check-in radius (tune in field test)
MATCH_BACK_M, MATCH_AHEAD_M = 200.0, 1000.0  # search window along the route around current progress


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
    new_visits: list[WaypointVisit] = []
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
        if acc is not None and acc <= ROUTE_MATCH_ACCURACY_M:
            new_visits += _match_route(db, s, lat, lon, acc, ts)
    db.commit()
    return {"accepted": accepted, "rejected": rejected, "distance_m": round(s.distance_m, 1),
            "new_waypoint_visits": [v.waypoint.name for v in new_visits]}


def _match_route(db: Session, s: TrackingSession, lat: float, lon: float, acc: float, ts: datetime) -> list[WaypointVisit]:
    """Advance along-route progress, track deviation and record waypoint check-ins."""
    route = s.route
    if not route or not route.has_geometry or not route.path:
        return []
    length = (route.distance_km or 0) * 1000
    cur = s.progress_m or 0.0
    dist, along = project_on_path(route.path, lat, lon, (max(cur - MATCH_BACK_M, 0.0), cur + MATCH_AHEAD_M))
    if dist <= OFF_ROUTE_M:
        s.progress_m = max(cur, along)
        s.off_route_since = None
    elif s.off_route_since is None:
        s.off_route_since = ts
    s.off_route_m = dist
    visited = {v.waypoint_id for v in s.visits}
    out = []
    for w in route.waypoints:
        if w.id in visited or w.lat is None or acc > WAYPOINT_RADIUS_M:
            continue
        if w.kind == "F" and (s.progress_m or 0) < 0.8 * length:
            continue  # loop route: the finish point equals the start
        if haversine_m(lat, lon, w.lat, w.lon) <= WAYPOINT_RADIUS_M:
            v = WaypointVisit(waypoint_id=w.id, waypoint=w, arrived_at=ts, accuracy_m=acc)
            s.visits.append(v)
            visited.add(w.id)
            out.append(v)
            if w.along_m is not None and w.kind != "S":
                s.progress_m = max(s.progress_m or 0.0, w.along_m)
    return out


def walked_m(s: TrackingSession) -> float:
    return s.manual_distance_m if s.tracking_mode == "manual" and s.manual_distance_m is not None else s.distance_m


def route_progress(s: TrackingSession) -> dict:
    """Progress along the route: path-based when geometry exists, else GPS distance / course length."""
    route = s.route
    if not route or not route.distance_km:
        return {"basis": "none", "done_m": None, "remaining_m": None, "ratio": None}
    length = route.distance_km * 1000
    if route.has_geometry and s.tracking_mode == "gps":
        done = min(s.progress_m or 0.0, length)
        basis = "route_geometry"
    else:
        done = min(walked_m(s), length)
        basis = "gps_distance" if s.tracking_mode == "gps" else "manual"
    return {"basis": basis, "done_m": done, "remaining_m": max(length - done, 0.0), "ratio": done / length}


def next_waypoint(s: TrackingSession) -> dict | None:
    route = s.route
    if not route or not route.waypoints:
        return None
    visited = {v.waypoint_id for v in s.visits}
    last_seq = max((v.waypoint.seq for v in s.visits if v.waypoint.kind == "W"), default=-1)
    done = s.progress_m or 0.0
    for w in route.waypoints:
        if w.id in visited or w.kind == "S" or w.seq < last_seq:
            continue
        if w.along_m is not None and w.along_m < done - MATCH_BACK_M:
            continue
        return {"id": w.id, "code": w.code, "name": w.name,
                "distance_m": round(max(w.along_m - done, 0)) if w.along_m is not None and route.has_geometry else None,
                "location": [w.lat, w.lon] if w.lat is not None else None}
    return None


def pace_targets(s: TrackingSession, prefs) -> dict | None:
    """Where the user's own settings say to stop next.

    * rest: a break every `rest_every_minutes` at `pace_kmh` → one rest segment = pace × interval.
    * day goal: `daily_distance_target_km` walked in this session.
    Both are capped at the course end. With path data each target snaps to the last waypoint at or
    before it (so you never plan to stop past your limit); without it only distances are given."""
    route = s.route
    prog = route_progress(s)
    if not route or prog["done_m"] is None:
        return None
    done, length = prog["done_m"], route.distance_km * 1000
    rest_every = prefs.rest_every_minutes or 0
    seg_m = prefs.pace_kmh * 1000 * rest_every / 60 if rest_every >= 30 else None
    goal_m = max(prefs.daily_distance_target_km * 1000 - walked_m(s), 0.0)
    rest_at = min(done + seg_m, length) if seg_m else None
    goal_at = min(done + goal_m, length)

    def snap(target: float | None) -> dict | None:
        if target is None or not route.has_geometry:
            return None
        cands = [w for w in route.waypoints if w.kind != "S" and w.along_m is not None and done < w.along_m <= target + 1]
        w = max(cands, key=lambda x: x.along_m, default=None)
        return {"name": w.name, "distance_m": round(w.along_m - done), "location": [w.lat, w.lon] if w.lat is not None else None} if w else None

    return {
        "rest_interval_min": rest_every or None,
        "rest_segment_km": round(seg_m / 1000, 2) if seg_m else None,
        "next_rest_in_km": round((rest_at - done) / 1000, 2) if rest_at is not None else None,
        "next_rest_point": snap(rest_at),
        "daily_target_km": prefs.daily_distance_target_km,
        "daily_remaining_km": round(goal_m / 1000, 2),
        "day_goal_point": snap(goal_at),
        "day_goal_reaches_course_end": goal_at >= length,
    }


NOTES = {
    "route_geometry": "진행률은 하영올레 경로 데이터(제주데이터허브 SHP) 위에 GPS 위치를 맞춰 계산했습니다.",
    "gps_distance": "진행률은 GPS 누적 거리를 코스 총거리로 나눈 추정치입니다(공식 경로 좌표 없음).",
    "manual": "진행률은 직접 입력한 거리 기준입니다.",
    "none": "이 코스는 거리 데이터가 없어 진행률을 계산하지 않습니다.",
}


def progress_summary(s: TrackingSession, schedule=None, now: datetime | None = None) -> dict:
    """Compact summary used by rules engine, UI and the AI context builder."""
    now = now or datetime.now(timezone.utc)
    prog = route_progress(s)
    expected_ratio = None
    if schedule and schedule.planned_start and schedule.planned_end and schedule.route_id == s.route_id:
        start = hhmm_to_minutes(schedule.planned_start)
        end_ = hhmm_to_minutes(schedule.planned_end)
        local = now.astimezone(KST)
        cur = local.hour * 60 + local.minute
        if end_ > start:
            expected_ratio = min(max((cur - start) / (end_ - start), 0.0), 1.0)
    acc = s.last_accuracy_m
    gps_quality = "unknown" if acc is None else ("good" if acc <= get_settings().min_gps_accuracy_m else "poor")
    stale = s.last_point_at is None or now - _aware(s.last_point_at) > timedelta(minutes=10)
    route = s.route
    return {
        "session_id": s.id,
        "status": s.status,
        "tracking_mode": s.tracking_mode,
        "route": route.name if route else None,
        "route_id": route.id if route else None,
        "trail_type": route.trail_type if route else None,
        "route_distance_km": route.distance_km if route else None,
        "walked_km": round(walked_m(s) / 1000, 2),
        "done_km": round(prog["done_m"] / 1000, 2) if prog["done_m"] is not None else None,
        "remaining_km": round(prog["remaining_m"] / 1000, 2) if prog["remaining_m"] is not None else None,
        "active_minutes": round(active_minutes(s, now)),
        "actual_progress": round(prog["ratio"], 3) if prog["ratio"] is not None else None,
        "expected_progress": round(expected_ratio, 3) if expected_ratio is not None else None,
        "progress_basis": prog["basis"],
        "next_waypoint": next_waypoint(s),
        "visited_waypoints": [v.waypoint.name for v in sorted(s.visits, key=lambda v: as_utc(v.arrived_at))],
        "off_route_m": round(s.off_route_m) if s.off_route_m is not None and route and route.has_geometry else None,
        "gps_quality": gps_quality,
        "gps_accuracy_m": round(acc) if acc is not None else None,
        "gps_stale": stale if s.tracking_mode == "gps" else False,
        "last_position": [round(s.last_lat, 4), round(s.last_lon, 4)] if s.last_lat is not None else None,
        "note": NOTES[prog["basis"]],
    }


def purge_old_points(db: Session, days: int = RAW_POINT_RETENTION_DAYS) -> int:
    """Minimum-retention policy for raw GPS (spec §09). Summaries on the session remain."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ended = select(TrackingSession.id).where(TrackingSession.ended_at.is_not(None), TrackingSession.ended_at < cutoff)
    res = db.execute(delete(GpsPoint).where(GpsPoint.session_id.in_(ended)))
    db.commit()
    return res.rowcount or 0
