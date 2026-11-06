"""Local single-user mode.

The app has no sign-in: it runs on one device (the Agent operating PC) for one person, so every request is
served as the one local user, which is created on first use. The `current_user` dependency is kept so that
every router still works on "the user's own" rows; to add real accounts later, only this module changes.
"""
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, UserPreferences


def local_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.created_at).limit(1))
    if user:
        return user
    user = User()
    user.preferences = UserPreferences()
    db.add(user)
    db.commit()
    return user


def current_user(db: Session = Depends(get_db)) -> User:
    return local_user(db)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not expected or x_admin_token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
