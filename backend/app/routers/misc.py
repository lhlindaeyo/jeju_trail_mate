"""Notifications, AI companion, account data rights, signed file access, admin data management."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent import orchestrator
from ..auth import current_user, require_admin
from ..db import get_db
from ..models import AuditEvent, Notification, RouteNotice, TrackingSession, Trip, User
from ..rules.engine import notification_to_dict
from ..services import storage as storage_mod
from ..services.route_service import find_route, route_to_dict
from ..services.storage import storage
from .common import audit, not_found, owned_session, owned_trip

router = APIRouter(prefix="/v1")

# --- notifications -----------------------------------------------------------------


@router.get("/notifications", tags=["notifications"])
def list_notifications(since: datetime | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == user.id)
    if since:
        q = q.where(Notification.created_at > since.astimezone(timezone.utc))
    items = list(db.scalars(q.order_by(Notification.created_at.desc()).limit(100)))
    now = datetime.now(timezone.utc)
    for n in items:
        if n.delivered_at is None:
            n.delivered_at = now
    db.commit()
    return {"notifications": [notification_to_dict(n) for n in items]}


class ActionIn(BaseModel):
    action: str = Field(pattern=r"^[a-z_]{1,30}$")


@router.post("/notifications/{notification_id}/action", tags=["notifications"])
def act(notification_id: str, body: ActionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise not_found()
    n.action_taken = body.action
    db.commit()
    return notification_to_dict(n)


# --- AI companion -------------------------------------------------------------------


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    trip_id: str | None = None
    session_id: str | None = None
    history: list[dict] = Field(default_factory=list, max_length=20)


@router.post("/agent/chat", tags=["agent"])
def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = owned_trip(db, user, body.trip_id) if body.trip_id else None
    session = owned_session(db, user, body.session_id) if body.session_id else None
    if session is None and trip is not None:
        session = db.scalar(select(TrackingSession).where(TrackingSession.trip_id == trip.id, TrackingSession.status != "ended"))
    if trip is None and session is not None:
        trip = session.trip
    return orchestrator.chat(db, user, body.message, trip, session, body.history)


# --- files (signed URLs) -----------------------------------------------------------


@router.get("/files", tags=["files"])
def get_file(key: str, exp: int, sig: str):
    if not storage_mod.verify(key, exp, sig):
        raise HTTPException(403, "link expired or invalid")
    try:
        data = storage.read(key)
    except (OSError, ValueError):
        raise not_found()
    ext = "." + key.rsplit(".", 1)[-1]
    ctype = next((ct for ct, (_k, e) in storage_mod.ALLOWED_TYPES.items() if e == ext), "application/octet-stream")
    if ctype == "text/html":
        ctype = "text/html; charset=utf-8"
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"})


# --- data rights (single local user) -------------------------------------------------------------


@router.get("/account/export", tags=["account"])
def export_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Machine-readable export of the user's own data (media referenced by short-lived links)."""
    from .me import prefs_to_dict
    from ..services.journal_service import entry_to_dict
    from ..services.schedule_service import plan_view

    trips = db.scalars(select(Trip).where(Trip.user_id == user.id))
    out = []
    for t in trips:
        out.append({
            "trip": {"id": t.id, "title": t.title, "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat()},
            "plan": plan_view(db, t),
            "journal": [entry_to_dict(e, {m.id: storage_mod.signed_url(m.storage_key) for m in e.media}) for e in t.journal_entries],
            "sessions": [{"id": s.id, "route": s.route.name if s.route else None, "started_at": s.started_at.isoformat(),
                          "ended_at": s.ended_at.isoformat() if s.ended_at else None, "distance_m": s.distance_m,
                          "points": [{"t": p.timestamp.isoformat(), "lat": p.lat, "lon": p.lon, "acc": p.accuracy_m} for p in s.points]}
                         for s in t.sessions],
        })
    audit(db, user.id, "data_exported", "user", user.id)
    db.commit()
    return {"profile": prefs_to_dict(user), "trips": out, "exported_at": datetime.now(timezone.utc).isoformat()}


@router.delete("/account/data", tags=["account"])
def delete_account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Erases the local user's rows (cascade), media and generated books. The next request starts a fresh profile."""
    files = storage.delete_prefix(user.id)
    uid = user.id
    db.delete(user)
    db.add(AuditEvent(user_id=uid, event_type="account_deleted", resource_type="user", resource_id=uid))
    db.commit()
    return {"deleted": True, "files_removed": files,
            "note": "이 기기에 저장된 여행·일기·사진·책을 모두 삭제했어요."}


# --- admin: verified course data & notices (spec §02 데이터 갱신 전략) ---------------


class RouteVerifyIn(BaseModel):
    distance_km: float | None = Field(default=None, gt=0, le=40)
    duration_min_low: int | None = Field(default=None, gt=0, le=900)
    duration_min_high: int | None = Field(default=None, gt=0, le=900)
    start_point: str | None = None
    end_point: str | None = None
    source_url: str | None = None


@router.post("/admin/routes/{route_ref}/verify", tags=["admin"], dependencies=[Depends(require_admin)])
def verify_route(route_ref: str, body: RouteVerifyIn, db: Session = Depends(get_db)):
    r = find_route(db, route_ref)
    if not r:
        raise not_found()
    changed = body.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changed.items():
        setattr(r, k, v)
    if changed:
        r.version += 1
    r.verification_status, r.checked_at = "verified", datetime.now(timezone.utc)
    audit(db, None, "route_verified", "trail_route", r.id)
    db.commit()
    return route_to_dict(r)


class NoticeIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str | None = None
    notice_type: str = Field(pattern=r"^(detour|closure|safety|info)$")
    source_url: str = Field(min_length=8, max_length=500)
    published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@router.post("/admin/routes/{route_ref}/notices", tags=["admin"], dependencies=[Depends(require_admin)], status_code=201)
def add_notice(route_ref: str, body: NoticeIn, db: Session = Depends(get_db)):
    r = find_route(db, route_ref)
    if not r:
        raise not_found()
    now = datetime.now(timezone.utc)
    n = RouteNotice(route_id=r.id, verified_at=now, **body.model_dump())
    r.notices_checked_at = now
    db.add(n)
    audit(db, None, "notice_added", "route_notice", n.id)
    db.commit()
    return {"id": n.id}


@router.post("/admin/routes/{route_ref}/notices-checked", tags=["admin"], dependencies=[Depends(require_admin)])
def mark_notices_checked(route_ref: str, db: Session = Depends(get_db)):
    """Record that an admin checked the official site and found no new notices."""
    r = find_route(db, route_ref)
    if not r:
        raise not_found()
    r.notices_checked_at = datetime.now(timezone.utc)
    db.commit()
    return route_to_dict(r)


@router.post("/admin/notices/{notice_id}/withdraw", tags=["admin"], dependencies=[Depends(require_admin)])
def withdraw_notice(notice_id: str, db: Session = Depends(get_db)):
    n = db.get(RouteNotice, notice_id)
    if not n:
        raise not_found()
    n.status = "withdrawn"
    db.commit()
    return {"id": n.id, "status": n.status}
