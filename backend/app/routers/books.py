from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import JourneyBook, User
from ..services import book_service
from ..services.storage import signed_url, storage
from .common import audit, owned_book, owned_trip

router = APIRouter(prefix="/v1", tags=["journey book"])


def _dict(b: JourneyBook) -> dict:
    return {
        "id": b.id, "trip_id": b.trip_id, "title": b.title, "theme": b.theme, "status": b.status, "error": b.error,
        "cover_media_id": b.cover_media_id, "prologue": b.prologue, "epilogue": b.epilogue,
        "excluded_entry_ids": b.excluded_entry_ids or [], "share_enabled": b.share_enabled,
        "generated_at": b.generated_at.isoformat() if b.generated_at else None,
        "approved_at": b.approved_at.isoformat() if b.approved_at else None,
        "preview_url": signed_url(b.storage_key) if b.storage_key and b.status in ("preview_ready", "user_approved", "exported") else None,
        "themes": list(book_service.THEMES),
    }


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    theme: str = "olle_green"
    cover_media_id: str | None = None
    prologue: str | None = Field(default=None, max_length=5000)
    epilogue: str | None = Field(default=None, max_length=5000)
    excluded_entry_ids: list[str] = Field(default_factory=list)


@router.get("/trips/{trip_id}/book/eligibility")
def book_eligibility(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return book_service.eligibility(db, owned_trip(db, user, trip_id))


@router.post("/trips/{trip_id}/book", status_code=201)
def create_book(trip_id: str, body: BookIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = owned_trip(db, user, trip_id)
    elig = book_service.eligibility(db, trip)
    if not elig["eligible"]:
        raise HTTPException(409, " ".join(elig["reasons"]))
    if body.theme not in book_service.THEMES:
        raise HTTPException(422, "unknown theme")
    b = JourneyBook(trip_id=trip.id, **body.model_dump())
    db.add(b)
    db.commit()
    return _dict(book_service.generate(db, trip, b))


@router.get("/books/{book_id}")
def get_book(book_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _dict(owned_book(db, user, book_id))


class BookPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    theme: str | None = None
    cover_media_id: str | None = None
    prologue: str | None = Field(default=None, max_length=5000)
    epilogue: str | None = Field(default=None, max_length=5000)
    excluded_entry_ids: list[str] | None = None


@router.patch("/books/{book_id}")
def update_book(book_id: str, body: BookPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Edits invalidate the previous preview → regenerate (user must approve again)."""
    b = owned_book(db, user, book_id)
    if body.theme and body.theme not in book_service.THEMES:
        raise HTTPException(422, "unknown theme")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    db.commit()
    return _dict(book_service.generate(db, b.trip, b))


@router.post("/books/{book_id}/regenerate")
def regenerate(book_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = owned_book(db, user, book_id)
    return _dict(book_service.generate(db, b.trip, b))


@router.post("/books/{book_id}/approve")
def approve(book_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = owned_book(db, user, book_id)
    if b.status != "preview_ready":
        raise HTTPException(409, f"book is {b.status}")
    b.status, b.approved_at = "user_approved", datetime.now(timezone.utc)
    audit(db, user.id, "book_approved", "journey_book", b.id)
    db.commit()
    return _dict(b)


@router.get("/books/{book_id}/export")
def export(book_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = owned_book(db, user, book_id)
    if b.status not in ("user_approved", "exported"):
        raise HTTPException(409, "미리보기를 확인하고 승인한 뒤 내보낼 수 있습니다.")
    pdf = book_service.export_pdf(b)
    b.status = "exported"
    db.commit()
    if pdf:
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="journey-book-{b.id}.pdf"'})
    # No server-side PDF engine: return the print-ready HTML; the client prints it to PDF.
    return HTMLResponse(storage.read(b.storage_key).decode("utf-8"), headers={"X-PDF-Engine": "browser-print"})


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = owned_book(db, user, book_id)
    if b.storage_key:
        storage.delete(b.storage_key)
    db.delete(b)
    db.commit()
