from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import route_service
from .common import not_found

router = APIRouter(prefix="/v1/routes", tags=["routes"])


@router.get("")
def list_routes(difficulty: str | None = None, max_km: float | None = None, tag: str | None = None, db: Session = Depends(get_db)):
    routes = route_service.list_routes(db, difficulty=difficulty, max_km=max_km, tag=tag)
    return {"routes": [route_service.route_to_dict(r) for r in routes], "official_url": route_service.OFFICIAL_TRAIL_URL,
            "disclaimer": "JEJU TRAIL MATE는 제주올레가 운영·보증하는 서비스가 아닙니다. 코스 정보는 공식 사이트에서 최종 확인하세요."}


@router.get("/{route_id}")
def get_route(route_id: str, db: Session = Depends(get_db)):
    r = route_service.find_route(db, route_id)
    if not r:
        raise not_found()
    return route_service.route_to_dict(r)


@router.get("/{route_id}/notices")
def get_notices(route_id: str, db: Session = Depends(get_db)):
    r = route_service.find_route(db, route_id)
    if not r:
        raise not_found()
    return route_service.notices_payload(db, r)
