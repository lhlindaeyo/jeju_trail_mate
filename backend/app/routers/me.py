from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import User
from .common import audit

router = APIRouter(prefix="/v1", tags=["profile"])

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class PreferencesIn(BaseModel):
    daily_distance_target_km: float | None = Field(default=None, ge=3, le=40)
    pace_kmh: float | None = Field(default=None, ge=1.5, le=7)
    walk_start_time: str | None = Field(default=None, pattern=HHMM)
    walk_end_time: str | None = Field(default=None, pattern=HHMM)
    rest_every_minutes: int | None = Field(default=None, ge=0, le=300)
    notification_settings: dict | None = None
    consent_location: bool | None = None
    consent_ai_processing: bool | None = None


def prefs_to_dict(user: User) -> dict:
    p = user.preferences
    return {
        "user_id": user.id, "locale": user.locale,
        "daily_distance_target_km": p.daily_distance_target_km, "pace_kmh": p.pace_kmh,
        "walk_start_time": p.walk_start_time, "walk_end_time": p.walk_end_time,
        "rest_every_minutes": p.rest_every_minutes,
        "notification_settings": p.notification_settings or {},
        "consents": {
            "location": p.consent_location,
            "ai_processing": p.consent_ai_processing,
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
    if any(k.startswith("consent_") for k in changes):
        audit(db, user.id, "consent_changed", "user_preferences", user.id)
    db.commit()
    return prefs_to_dict(user)
