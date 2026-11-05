from datetime import date as Date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import get_settings
from ..db import get_db
from ..models import JournalEntry, MediaAsset, User
from ..services import journal_service
from ..services.geo import now_kst
from ..services.storage import ALLOWED_TYPES, signed_url, storage
from .common import not_found, owned_entry, owned_trip

router = APIRouter(prefix="/v1", tags=["journal"])


def _dict(e: JournalEntry) -> dict:
    return journal_service.entry_to_dict(e, {m.id: signed_url(m.storage_key) for m in e.media})


class EntryIn(BaseModel):
    trip_id: str
    date: Date | None = None
    text: str | None = Field(default=None, max_length=10000)
    source: str = Field(default="text", pattern=r"^(text|voice)$")
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    captured_at: datetime | None = None
    is_highlight: bool = False


@router.post("/journals", status_code=201)
def create_entry(body: EntryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = owned_trip(db, user, body.trip_id)
    if body.source == "voice" and not user.preferences.consent_microphone:
        raise HTTPException(403, "음성 기록 동의가 필요합니다.")
    keep_location = user.preferences.consent_location
    e = JournalEntry(
        trip_id=trip.id, date=body.date or now_kst().date(), kind="memo", text=body.text,
        lat=body.lat if keep_location else None, lon=body.lon if keep_location else None,
        is_highlight=body.is_highlight, status="saved",  # a user's own words are their record
    )
    if body.captured_at:
        e.captured_at = body.captured_at
    db.add(e)
    db.commit()
    return _dict(e)


@router.get("/trips/{trip_id}/journals")
def list_entries(trip_id: str, day: Date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = owned_trip(db, user, trip_id)
    q = select(JournalEntry).where(JournalEntry.trip_id == trip.id)
    if day:
        q = q.where(JournalEntry.date == day)
    entries = db.scalars(q.order_by(JournalEntry.date, JournalEntry.captured_at))
    return {"entries": [_dict(e) for e in entries]}


class DailyDraftIn(BaseModel):
    date: Date


@router.post("/trips/{trip_id}/journals/daily-draft")
def daily_draft(trip_id: str, body: DailyDraftIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = owned_trip(db, user, trip_id)
    try:
        e = journal_service.draft_daily(db, trip, body.date, user.preferences)
    except journal_service.NotEnoughRecords as exc:
        raise HTTPException(422, str(exc))
    return _dict(e)


@router.post("/journals/{entry_id}/draft")
def entry_draft(entry_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned_entry(db, user, entry_id)
    try:
        if e.kind == "daily":
            e = journal_service.draft_daily(db, e.trip, e.date, user.preferences)
        else:
            e = journal_service.polish_memo(db, e, user.preferences)
    except journal_service.NotEnoughRecords as exc:
        raise HTTPException(422, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return _dict(e)


class SaveIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    mood_tags: list[str] | None = Field(default=None, max_length=10)
    is_highlight: bool | None = None


@router.put("/journals/{entry_id}")
def save_entry(entry_id: str, body: SaveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned_entry(db, user, entry_id)
    return _dict(journal_service.save_entry(db, e, body.text, body.mood_tags, body.is_highlight))


@router.delete("/journals/{entry_id}", status_code=204)
def delete_entry(entry_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned_entry(db, user, entry_id)
    for m in e.media:
        storage.delete(m.storage_key)
    db.delete(e)
    db.commit()


@router.post("/journals/{entry_id}/media", status_code=201)
async def upload_media(entry_id: str, file: UploadFile = File(...), caption: str | None = Form(default=None),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned_entry(db, user, entry_id)
    ctype = (file.content_type or "").split(";")[0].strip()
    if ctype not in ALLOWED_TYPES or ALLOWED_TYPES[ctype][0] not in ("photo", "audio"):
        raise HTTPException(415, "지원하지 않는 파일 형식입니다.")
    kind = ALLOWED_TYPES[ctype][0]
    p = user.preferences
    if (kind == "photo" and not p.consent_photos) or (kind == "audio" and not p.consent_microphone):
        raise HTTPException(403, "사진/음성 저장 동의가 필요합니다.")
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "파일이 너무 큽니다.")
    key = storage.put(user.id, data, ctype)
    m = MediaAsset(entry_id=e.id, storage_key=key, media_type=kind, content_type=ctype,
                   caption=(caption or None) and caption[:1000])
    db.add(m)
    db.commit()
    db.refresh(e)
    return _dict(e)


@router.delete("/media/{media_id}", status_code=204)
def delete_media(media_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.get(MediaAsset, media_id)
    if not m or m.entry.trip.user_id != user.id:
        raise not_found()
    storage.delete(m.storage_key)
    db.delete(m)
    db.commit()
