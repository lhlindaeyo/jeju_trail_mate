"""Who is asking: the one local user, or (public demo) one user per browser.

There is no sign-in. By default the app runs on one device (the Agent operating PC) for one person, so every
request is served as the one local user, which is created on first use.

With PER_DEVICE_USERS=1 (set by `npm run share` when the app is opened to judges over a public URL) each
browser sends a random X-Device-Id it keeps in localStorage and gets its own user, so testers do not share
trips, sessions or progress. This is separation, not authentication: anyone holding a device id is that user.

The `current_user` dependency is kept so that every router works on "the user's own" rows; real accounts
later only change this module (User.provider_id is already there for them).
"""
import re

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, UserPreferences

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
DEVICE_PREFIX = "device:"


def _new_user(db: Session, provider_id: str | None = None) -> User:
    user = User(provider_id=provider_id)
    user.preferences = UserPreferences()
    db.add(user)
    db.commit()
    return user


def local_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.provider_id.is_(None)).order_by(User.created_at).limit(1))
    return user or _new_user(db)


def device_user(db: Session, device_id: str) -> User:
    key = DEVICE_PREFIX + device_id
    user = db.scalar(select(User).where(User.provider_id == key))
    if user:
        return user
    try:
        return _new_user(db, key)
    except IntegrityError:  # the browser's first requests arrive in parallel; another one created it
        db.rollback()
        return db.scalars(select(User).where(User.provider_id == key)).one()


def current_user(db: Session = Depends(get_db), x_device_id: str | None = Header(default=None)) -> User:
    if not get_settings().per_device_users:
        return local_user(db)
    if not x_device_id or not DEVICE_ID_RE.match(x_device_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Device-Id header required")
    return device_user(db, x_device_id)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not expected or x_admin_token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
