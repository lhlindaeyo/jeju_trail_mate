from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import TrackingSession, Trip, User
from ..services import brief_service
from ..services.route_service import find_route
from .common import not_found, owned_trip

router = APIRouter(prefix="/v1", tags=["brief"])


@router.get("/brief")
def brief(trip_id: str | None = None, route_id: str | None = None,
          lat: float | None = Query(default=None, ge=-90, le=90), lon: float | None = Query(default=None, ge=-180, le=180),
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """앞길 브리핑. 진행 중인 세션이 있으면 그 코스·위치 기준, 없으면 route_id(없으면 오늘 일정)와 기기 위치(lat, lon) 기준."""
    session = db.scalar(select(TrackingSession).join(Trip).where(Trip.user_id == user.id, TrackingSession.status != "ended"))
    trip = session.trip if session else (owned_trip(db, user, trip_id) if trip_id else None)
    route = None
    if route_id:
        route = find_route(db, route_id)
        if route is None:
            raise not_found()
    return brief_service.build_brief(db, user, trip, session, route, lat, lon)
