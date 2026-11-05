from datetime import date as Date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import AgentProposal, Trip, User
from ..services import schedule_service
from ..services.geo import now_kst
from .common import audit, not_found, owned_trip

router = APIRouter(prefix="/v1/trips", tags=["trips & plan"])
MAX_TRIP_DAYS = 30


class TripIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_date: Date
    end_date: Date
    lodging_note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on/after start_date")
        if (self.end_date - self.start_date).days + 1 > MAX_TRIP_DAYS:
            raise ValueError(f"trip may span at most {MAX_TRIP_DAYS} days")
        return self


def trip_to_dict(t: Trip) -> dict:
    return {"id": t.id, "title": t.title, "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat(),
            "lodging_note": t.lodging_note, "status": t.status}


@router.post("", status_code=201)
def create_trip(body: TripIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = Trip(user_id=user.id, **body.model_dump())
    db.add(t)
    db.commit()
    return trip_to_dict(t)


@router.get("")
def list_trips(user: User = Depends(current_user), db: Session = Depends(get_db)):
    trips = db.scalars(select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_date.desc()))
    return {"trips": [trip_to_dict(t) for t in trips]}


@router.get("/{trip_id}")
def get_trip(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    return {**trip_to_dict(t), "plan": schedule_service.plan_view(db, t)}


class TripPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    lodging_note: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern=r"^(planning|active|completed)$")


@router.patch("/{trip_id}")
def update_trip(trip_id: str, body: TripPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    db.commit()
    return trip_to_dict(t)


@router.delete("/{trip_id}", status_code=204)
def delete_trip(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services.storage import storage

    t = owned_trip(db, user, trip_id)
    for e in t.journal_entries:
        for m in e.media:
            storage.delete(m.storage_key)
    for b in t.books:
        if b.storage_key:
            storage.delete(b.storage_key)
    audit(db, user.id, "trip_deleted", "trip", t.id)
    db.delete(t)
    db.commit()


@router.get("/{trip_id}/today")
def today(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Home-screen briefing: today's approved plan + data freshness (spec journey step 2)."""
    from ..services.geo import sunset_kst
    from ..services.route_service import notices_payload, route_to_dict

    t = owned_trip(db, user, trip_id)
    d = now_kst().date()
    sched = schedule_service.approved_for(db, t, d)
    out = {"date": d.isoformat(), "sunset": sunset_kst(d).strftime("%H:%M"),
           "schedule": schedule_service.schedule_to_dict(sched) if sched else None}
    if sched and sched.route:
        out["route"] = route_to_dict(sched.route, with_path=True)
        out["notices"] = notices_payload(db, sched.route)
        if sched.route.distance_km:
            out["estimate"] = {
                "duration_min": schedule_service.estimate_duration_min(sched.route.distance_km, user.preferences.pace_kmh, user.preferences.rest_every_minutes),
                "assumptions": f"평균 {user.preferences.pace_kmh}km/h, 휴식 포함",
            }
    return out


class PlanIn(BaseModel):
    route_ids: list[str] | None = Field(default=None, max_length=MAX_TRIP_DAYS * 3)
    rest_days: list[Date] | None = None
    trail_type: str = Field(default="jeju_olle", pattern=r"^(jeju_olle|hayoung_olle)$")


@router.post("/{trip_id}/plan")
def generate_plan(trip_id: str, body: PlanIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    drafts = schedule_service.generate_plan(db, t, user.preferences, body.route_ids, body.rest_days, body.trail_type)
    return {"drafts": [schedule_service.schedule_to_dict(d) for d in drafts],
            "message": "일정 초안입니다. 확인 후 승인해야 확정됩니다."}


class ChangeIn(BaseModel):
    date: Date
    route_id: str | None = None
    extra_route_ids: list[str] = Field(default_factory=list, max_length=3)
    planned_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    note: str | None = Field(default=None, max_length=500)


@router.post("/{trip_id}/plan/changes")
def change_plan(trip_id: str, changes: list[ChangeIn], user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    try:
        drafts = schedule_service.propose_changes(db, t, user.preferences, [c.model_dump() for c in changes])
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"drafts": [schedule_service.schedule_to_dict(d) for d in drafts]}


class ApproveIn(BaseModel):
    dates: list[Date] | None = None


@router.post("/{trip_id}/plan/approve")
def approve_plan(trip_id: str, body: ApproveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    approved = schedule_service.approve(db, t, body.dates)
    audit(db, user.id, "plan_approved", "trip", t.id)
    db.commit()
    return {"approved": [schedule_service.schedule_to_dict(a) for a in approved], "plan": schedule_service.plan_view(db, t)}


@router.get("/{trip_id}/proposals")
def list_proposals(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    return {"proposals": [{"id": p.id, "summary": p.summary, "payload": p.payload, "status": p.status,
                           "created_at": p.created_at.isoformat()} for p in t.proposals]}


class DecisionIn(BaseModel):
    approve: bool


@router.post("/{trip_id}/proposals/{proposal_id}/decision")
def decide_proposal(trip_id: str, proposal_id: str, body: DecisionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = owned_trip(db, user, trip_id)
    p = db.get(AgentProposal, proposal_id)
    if not p or p.trip_id != t.id:
        raise not_found()
    if p.status != "pending":
        raise HTTPException(409, f"proposal already {p.status}")
    p.decided_at = datetime.now(timezone.utc)
    if not body.approve:
        p.status = "rejected"
        db.commit()
        return {"status": p.status}
    drafts = schedule_service.propose_changes(db, t, user.preferences, p.payload["changes"])
    schedule_service.approve(db, t, [d.date for d in drafts])
    p.status = "approved"
    audit(db, user.id, "proposal_approved", "agent_proposal", p.id)
    db.commit()
    return {"status": p.status, "plan": schedule_service.plan_view(db, t)}
