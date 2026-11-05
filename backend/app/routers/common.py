"""Ownership checks: every user-owned resource is looked up through its owning trip, and a
mismatch returns 404 (no existence leak)."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AuditEvent, JournalEntry, JourneyBook, TrackingSession, Trip, User


def not_found() -> HTTPException:
    return HTTPException(404, "not found")


def owned_trip(db: Session, user: User, trip_id: str) -> Trip:
    trip = db.get(Trip, trip_id)
    if not trip or trip.user_id != user.id:
        raise not_found()
    return trip


def owned_session(db: Session, user: User, session_id: str) -> TrackingSession:
    s = db.get(TrackingSession, session_id)
    if not s or s.trip.user_id != user.id:
        raise not_found()
    return s


def owned_entry(db: Session, user: User, entry_id: str) -> JournalEntry:
    e = db.get(JournalEntry, entry_id)
    if not e or e.trip.user_id != user.id:
        raise not_found()
    return e


def owned_book(db: Session, user: User, book_id: str) -> JourneyBook:
    b = db.get(JourneyBook, book_id)
    if not b or b.trip.user_id != user.id:
        raise not_found()
    return b


def audit(db: Session, user_id: str | None, event_type: str, resource_type: str, resource_id: str | None = None) -> None:
    db.add(AuditEvent(user_id=user_id, event_type=event_type, resource_type=resource_type, resource_id=resource_id))
