from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import TrackingSession, Trip, User
from ..rules import engine
from ..services import schedule_service, tracking_service
from ..services.geo import now_kst
from ..services.route_service import find_route
from .common import owned_session, owned_trip

router = APIRouter(prefix="/v1/sessions", tags=["tracking"])
MAX_BATCH = 500


class SessionIn(BaseModel):
    trip_id: str
    route_id: str | None = None
    tracking_mode: str = Field(default="gps", pattern=r"^(gps|manual)$")


def _summary(db: Session, s: TrackingSession) -> dict:
    sched = schedule_service.approved_for(db, s.trip, now_kst().date())
    return tracking_service.progress_summary(s, sched)


@router.post("", status_code=201)
def start(body: SessionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = owned_trip(db, user, body.trip_id)
    if body.tracking_mode == "gps" and not user.preferences.consent_location:
        raise HTTPException(409, "위치 기록 동의가 필요합니다. 동의하지 않으면 수동 기록(tracking_mode=manual)으로 진행할 수 있습니다.")
    open_ = db.scalar(select(TrackingSession).join(Trip).where(Trip.user_id == user.id, TrackingSession.status != "ended"))
    if open_:
        raise HTTPException(409, f"진행 중인 세션이 있습니다: {open_.id}")
    route = None
    if body.route_id:
        route = find_route(db, body.route_id)
    else:
        sched = schedule_service.approved_for(db, trip, now_kst().date())
        route = sched.route if sched else None
    s = TrackingSession(trip_id=trip.id, route_id=route.id if route else None, tracking_mode=body.tracking_mode)
    db.add(s)
    trip.status = "active"
    db.commit()
    notes = engine.deliver(db, s, user, engine.evaluate(db, s, user))
    return {**_summary(db, s), "notifications": [engine.notification_to_dict(n) for n in notes]}


@router.get("/current")
def current(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.scalar(select(TrackingSession).join(Trip).where(Trip.user_id == user.id, TrackingSession.status != "ended"))
    return {"session": _summary(db, s) if s else None}


@router.get("/{session_id}")
def get_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _summary(db, owned_session(db, user, session_id))


class PointIn(BaseModel):
    timestamp: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=10000)
    source: Literal["device", "sim"] = "device"  # "sim" = demo location simulator in the web app


class PointsIn(BaseModel):
    points: list[PointIn] = Field(max_length=MAX_BATCH)


@router.post("/{session_id}/points")
def upload_points(session_id: str, body: PointsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = owned_session(db, user, session_id)
    if s.tracking_mode != "gps":
        raise HTTPException(409, "manual session does not accept GPS points")
    if not user.preferences.consent_location:
        raise HTTPException(403, "위치 기록 동의가 철회되어 위치를 저장하지 않습니다.")
    result = tracking_service.ingest_points(db, s, [p.model_dump() for p in body.points])
    notes = engine.on_location_batch(db, s, user)
    return {**result, "summary": _summary(db, s), "notifications": [engine.notification_to_dict(n) for n in notes]}


class ManualIn(BaseModel):
    distance_km: float = Field(ge=0, le=60)


@router.post("/{session_id}/manual")
def manual_progress(session_id: str, body: ManualIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = owned_session(db, user, session_id)
    s.manual_distance_m = body.distance_km * 1000
    db.commit()
    notes = engine.deliver(db, s, user, engine.evaluate(db, s, user))
    return {**_summary(db, s), "notifications": [engine.notification_to_dict(n) for n in notes]}


@router.post("/{session_id}/{action}")
def control(session_id: str, action: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = owned_session(db, user, session_id)
    fn = {"pause": tracking_service.pause, "resume": tracking_service.resume, "end": tracking_service.end}.get(action)
    if not fn:
        raise HTTPException(404, "unknown action")
    fn(s)
    db.commit()
    notes = engine.deliver(db, s, user, engine.evaluate(db, s, user)) if action == "end" else []
    return {**_summary(db, s), "notifications": [engine.notification_to_dict(n) for n in notes]}
