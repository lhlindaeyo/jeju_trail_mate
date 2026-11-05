"""Course metadata and official notices (spec §02). Data is admin-managed and versioned;
no scraping. Every response carries source + freshness so the UI can show
'최신 확인 필요' badges."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RouteNotice, TrailRoute

OFFICIAL_TRAIL_URL = "https://www.jejuolle.org/trail#/"
ROUTE_STALE_AFTER = timedelta(days=90)
NOTICE_CHECK_STALE_AFTER = timedelta(hours=24)
SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "seed_routes.json"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def seed_routes(db: Session) -> int:
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    added = 0
    for r in data["routes"]:
        if db.scalar(select(TrailRoute).where(TrailRoute.official_code == r["official_code"])):
            continue
        db.add(TrailRoute(**r, source_url=OFFICIAL_TRAIL_URL, verification_status="unverified"))
        added += 1
    db.commit()
    return added


def route_freshness(route: TrailRoute, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    checked = _aware(route.checked_at)
    notices_checked = _aware(route.notices_checked_at)
    data_ok = route.verification_status == "verified" and checked is not None and now - checked < ROUTE_STALE_AFTER
    notices_ok = notices_checked is not None and now - notices_checked < NOTICE_CHECK_STALE_AFTER
    return {
        "data_status": "verified" if data_ok else "needs_check",
        "checked_at": checked.isoformat() if checked else None,
        "notices_status": "checked" if notices_ok else "needs_check",
        "notices_checked_at": notices_checked.isoformat() if notices_checked else None,
        "source_url": route.source_url,
    }


def route_to_dict(route: TrailRoute) -> dict:
    return {
        "id": route.id,
        "official_code": route.official_code,
        "name": route.name,
        "start_point": route.start_point,
        "end_point": route.end_point,
        "start": [route.start_lat, route.start_lon] if route.start_lat is not None else None,
        "end": [route.end_lat, route.end_lon] if route.end_lat is not None else None,
        "distance_km": route.distance_km,
        "difficulty": route.difficulty,
        "estimated_duration_min": route.estimated_duration_min,
        "tags": route.tags or [],
        "version": route.version,
        "freshness": route_freshness(route),
    }


def list_routes(db: Session, *, difficulty: str | None = None, max_km: float | None = None, tag: str | None = None) -> list[TrailRoute]:
    stmt = select(TrailRoute)
    if difficulty:
        stmt = stmt.where(TrailRoute.difficulty == difficulty)
    if max_km:
        stmt = stmt.where(TrailRoute.distance_km <= max_km)
    routes = list(db.scalars(stmt))
    if tag:
        routes = [r for r in routes if tag in (r.tags or [])]

    def sort_key(r: TrailRoute):
        return (int(r.official_code) if r.official_code.isdigit() else 999, r.official_code)

    return sorted(routes, key=sort_key)


def find_route(db: Session, route_ref: str) -> TrailRoute | None:
    """Accepts an internal id or an official course code such as '7'."""
    route = db.get(TrailRoute, route_ref)
    if route:
        return route
    code = route_ref.replace("코스", "").strip()
    return db.scalar(select(TrailRoute).where(TrailRoute.official_code == code))


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
    if fresh["notices_status"] != "checked":
        caveat = (
            "최근 24시간 내 공식 공지 확인 기록이 없습니다. 출발 전 제주올레 공식 사이트에서 "
            "우회·통제 공지를 직접 확인하세요."
        )
    return {
        "route": route.name,
        "notices": notices,
        "notices_status": fresh["notices_status"],
        "notices_checked_at": fresh["notices_checked_at"],
        "official_url": OFFICIAL_TRAIL_URL,
        "caveat": caveat,
    }
