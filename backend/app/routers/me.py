from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..auth import current_user, get_or_create_user, issue_dev_token
from ..config import get_settings
from ..db import get_db
from ..models import User
from .common import audit

router = APIRouter(prefix="/v1", tags=["auth & profile"])

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class DevLogin(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")


@router.post("/auth/dev-login")
def dev_login(body: DevLogin, db: Session = Depends(get_db)):
    if get_settings().app_env == "production":
        raise HTTPException(404, "not found")
    user = get_or_create_user(db, email=body.email.lower())
    return {"access_token": issue_dev_token(user), "token_type": "bearer", "user_id": user.id}


class PreferencesIn(BaseModel):
    daily_distance_target_km: float | None = Field(default=None, ge=3, le=40)
    pace_kmh: float | None = Field(default=None, ge=1.5, le=7)
    walk_start_time: str | None = Field(default=None, pattern=HHMM)
    walk_end_time: str | None = Field(default=None, pattern=HHMM)
    rest_every_minutes: int | None = Field(default=None, ge=0, le=300)
    interests: list[str] | None = Field(default=None, max_length=20)
    notification_settings: dict | None = None
    journal_style: str | None = Field(default=None, max_length=200)
    consent_location: bool | None = None
    consent_microphone: bool | None = None
    consent_photos: bool | None = None
    consent_ai_processing: bool | None = None
    consent_memory: bool | None = None

    @field_validator("interests")
    @classmethod
    def _short(cls, v):
        return [s[:20] for s in v] if v else v


def prefs_to_dict(user: User) -> dict:
    p = user.preferences
    return {
        "user_id": user.id, "email": user.email, "locale": user.locale,
        "daily_distance_target_km": p.daily_distance_target_km, "pace_kmh": p.pace_kmh,
        "walk_start_time": p.walk_start_time, "walk_end_time": p.walk_end_time,
        "rest_every_minutes": p.rest_every_minutes, "interests": p.interests or [],
        "notification_settings": p.notification_settings or {}, "journal_style": p.journal_style,
        "consents": {
            "location": p.consent_location, "microphone": p.consent_microphone, "photos": p.consent_photos,
            "ai_processing": p.consent_ai_processing, "memory": p.consent_memory,
        },
    }


@router.get("/me")
def me(user: User = Depends(current_user)):
    from ..agent.llm import get_llm

    llm = get_llm()
    return {**prefs_to_dict(user), "ai_provider": llm.name, "ai_external": llm.external}


@router.patch("/me/preferences")
def update_preferences(body: PreferencesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = user.preferences
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(p, k, v)
    if changes.get("consent_memory") is False:
        # Revoking memory consent erases stored personalization.
        p.interests, p.journal_style = [], None
    if any(k.startswith("consent_") for k in changes):
        audit(db, user.id, "consent_changed", "user_preferences", user.id)
    db.commit()
    return prefs_to_dict(user)
