"""Course metadata and official notices (spec §02). Data is admin-managed and versioned;
no scraping. Every response carries source + freshness so the UI can show
'최신 확인 필요' badges."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataSource, RouteNotice, RouteWaypoint, TrailRoute

OFFICIAL_TRAIL_URL = "https://www.jejuolle.org/trail#/"
HAYOUNG_INFO_URL = "https://www.jejudatahub.net/"
ROUTE_STALE_AFTER = timedelta(days=90)
NOTICE_CHECK_STALE_AFTER = timedelta(hours=24)
SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "seed_routes.json"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


TRAIL_TYPES = {
    "jeju_olle": {"label": "제주올레", "operator": "사단법인 제주올레"},
    "hayoung_olle": {"label": "하영올레", "operator": "서귀포시",
                     "notice": "서귀포시가 운영하는 도심 올레길로, 제주올레 공식 코스와는 별개입니다."},
}


def seed_routes(db: Session) -> int:
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    for src in data["data_sources"]:
        if not db.get(DataSource, src["id"]):
            db.add(DataSource(**src))
    db.flush()
    added = 0
    for r in data["routes"]:
        exists = db.scalar(select(TrailRoute).where(TrailRoute.trail_type == r["trail_type"], TrailRoute.course_no == r["course_no"]))
        if exists:
            continue
        r = dict(r)
        waypoints = r.pop("waypoints", [])
        subtitle = r.pop("subtitle", None)
        route = TrailRoute(**r, tags=[subtitle] if subtitle else [], verification_status="unverified",
                           source_url=OFFICIAL_TRAIL_URL if r["trail_type"] == "jeju_olle" else HAYOUNG_INFO_URL)
        route.waypoints = [RouteWaypoint(**w, source_id=r["source_id"]) for w in waypoints]
        db.add(route)
        added += 1
    db.commit()
    return added


def source_to_dict(src: DataSource | None) -> dict | None:
    if not src:
        return None
    return {"id": src.id, "name": src.name, "provider": src.provider, "url": src.platform_url,
            "ref_date": src.ref_date, "license": src.license, "status": src.status,
            "downloaded_at": _aware(src.downloaded_at).isoformat() if src.downloaded_at else None}


def route_freshness(route: TrailRoute, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    checked = _aware(route.checked_at)
    notices_checked = _aware(route.notices_checked_at)
    data_ok = route.verification_status == "verified" and checked is not None and now - checked < ROUTE_STALE_AFTER
    notices_ok = notices_checked is not None and now - notices_checked < NOTICE_CHECK_STALE_AFTER
    src = route.source
    return {
        "data_status": "verified" if data_ok else "needs_check",
        "checked_at": checked.isoformat() if checked else None,
        "ref_date": src.ref_date if src else None,
        "source": source_to_dict(src),
        "notices_status": "checked" if notices_ok else "needs_check",
        "notices_checked_at": notices_checked.isoformat() if notices_checked else None,
        "source_url": route.source_url,
    }


def waypoint_to_dict(w) -> dict:
    return {"id": w.id, "seq": w.seq, "code": w.code, "name": w.name, "kind": w.kind,
            "location": [w.lat, w.lon] if w.lat is not None else None,
            "along_m": round(w.along_m) if w.along_m is not None else None}


def route_to_dict(route: TrailRoute, *, with_path: bool = False) -> dict:
    meta = TRAIL_TYPES.get(route.trail_type, {})
    out = {
        "id": route.id,
        "trail_type": route.trail_type,
        "trail_label": meta.get("label", route.trail_type),
        "operator": route.operator,
        "type_notice": meta.get("notice"),
        "course_no": route.course_no,
        "official_code": route.course_no,
        "name": route.name,
        "subtitle": (route.tags or [None])[0],
        "start_point": route.start_point,
        "end_point": route.end_point,
        "start": [route.start_lat, route.start_lon] if route.start_lat is not None else None,
        "end": [route.end_lat, route.end_lon] if route.end_lat is not None else None,
        "distance_km": route.distance_km,
        "duration_min_low": route.duration_min_low,
        "duration_min_high": route.duration_min_high,
        "estimated_duration_min": route.estimated_duration_min,
        "has_geometry": route.has_geometry,
        "waypoints": [waypoint_to_dict(w) for w in route.waypoints],
        "version": route.version,
        "freshness": route_freshness(route),
    }
    if with_path and route.has_geometry:
        out["path"] = route.path
    return out


def list_routes(db: Session, *, trail_type: str | None = None, max_km: float | None = None,
                tag: str | None = None, **_ignored) -> list[TrailRoute]:
    stmt = select(TrailRoute)
    if trail_type:
        stmt = stmt.where(TrailRoute.trail_type == trail_type)
    if max_km:
        stmt = stmt.where(TrailRoute.distance_km <= max_km)
    routes = list(db.scalars(stmt))
    if tag:
        routes = [r for r in routes if any(tag in t for t in (r.tags or []))]

    def sort_key(r: TrailRoute):
        main, _, sub = r.course_no.partition("-")
        return (r.trail_type != "jeju_olle", int(main) if main.isdigit() else 999, sub)

    return sorted(routes, key=sort_key)


def find_route(db: Session, route_ref: str, trail_type: str | None = None) -> TrailRoute | None:
    """Accepts an internal id, '7', '7코스', '하영올레 2코스', 'hayoung_olle:2'."""
    route = db.get(TrailRoute, route_ref)
    if route:
        return route
    ref = route_ref.strip()
    if ":" in ref:
        trail_type, ref = ref.split(":", 1)
    if "하영" in ref:
        trail_type = "hayoung_olle"
    code = ref.replace("하영올레", "").replace("제주올레", "").replace("올레", "").replace("코스", "").strip()
    stmt = select(TrailRoute).where(TrailRoute.course_no == code,
                                    TrailRoute.trail_type == (trail_type or "jeju_olle"))
    return db.scalar(stmt)


def active_notices(db: Session, route: TrailRoute, now: datetime | None = None) -> list[RouteNotice]:
    now = now or datetime.now(timezone.utc)
    out = []
    for n in route.notices:
        if n.status != "active":
            continue
        if n.valid_until and _aware(n.valid_until) < now:
            continue
        if n.valid_from and _aware(n.valid_from) > now:
            continue
        out.append(n)
    return out


def notices_payload(db: Session, route: TrailRoute) -> dict:
    """Notices plus an honest statement of how fresh our knowledge is."""
    fresh = route_freshness(route)
    notices = [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "notice_type": n.notice_type,
            "source_url": n.source_url,
            "published_at": n.published_at.isoformat() if n.published_at else None,
            "valid_until": n.valid_until.isoformat() if n.valid_until else None,
            "verified_at": n.verified_at.isoformat() if n.verified_at else None,
        }
        for n in active_notices(db, route)
    ]
    caveat = None
    where = "제주올레 공식 사이트" if route.trail_type == "jeju_olle" else "서귀포시 공지"
    if fresh["notices_status"] != "checked":
        caveat = f"최근 24시간 내 공지 확인 기록이 없습니다. 출발 전 {where}에서 우회·통제 공지를 직접 확인하세요."
    return {
        "route": route.name,
        "trail_type": route.trail_type,
        "notices": notices,
        "notices_status": fresh["notices_status"],
        "notices_checked_at": fresh["notices_checked_at"],
        "official_url": OFFICIAL_TRAIL_URL if route.trail_type == "jeju_olle" else "https://www.seogwipo.go.kr/",
        "caveat": caveat,
    }
