from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import DataSource, PoiPlace, User
from ..services import route_service, schedule_service
from ..services.geo import now_kst
from .common import not_found

router = APIRouter(prefix="/v1", tags=["routes & data"])

DISCLAIMER = ("JEJU TRAIL MATE는 사단법인 제주올레·서귀포시가 운영·보증하는 서비스가 아닙니다. "
              "하영올레는 서귀포시가 운영하는 도심 올레길로 제주올레 공식 코스와 별개입니다.")


@router.get("/routes")
def list_routes(trail_type: str | None = Query(default=None, pattern=r"^(jeju_olle|hayoung_olle)$"),
                max_km: float | None = None, db: Session = Depends(get_db)):
    routes = route_service.list_routes(db, trail_type=trail_type, max_km=max_km)
    return {"routes": [route_service.route_to_dict(r) for r in routes],
            "trail_types": route_service.TRAIL_TYPES, "official_url": route_service.OFFICIAL_TRAIL_URL,
            "disclaimer": DISCLAIMER}


@router.get("/routes/hayoung/bundles")
def hayoung_bundles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"bundles": schedule_service.hayoung_bundles(db, user.preferences, now_kst().date())}


@router.get("/routes/{route_id}")
def get_route(route_id: str, db: Session = Depends(get_db)):
    r = route_service.find_route(db, route_id)
    if not r:
        raise not_found()
    return route_service.route_to_dict(r, with_path=True)


@router.get("/routes/{route_id}/notices")
def get_notices(route_id: str, db: Session = Depends(get_db)):
    r = route_service.find_route(db, route_id)
    if not r:
        raise not_found()
    return route_service.notices_payload(db, r)


@router.get("/data-sources")
def data_sources(db: Session = Depends(get_db)):
    """In-app '데이터 출처' screen: provenance, reference dates and terms of every dataset."""
    out = []
    for src in db.scalars(select(DataSource).order_by(DataSource.id)):
        d = route_service.source_to_dict(src)
        d["note"] = src.note
        d["poi_count"] = db.query(PoiPlace).filter(PoiPlace.source_id == src.id).count()
        out.append(d)
    return {"sources": out, "disclaimer": DISCLAIMER}
