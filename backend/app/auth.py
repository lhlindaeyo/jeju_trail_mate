"""Authentication.

* Development: `POST /v1/auth/dev-login` issues a short-lived HS256 token (disabled in production).
* Production: set SUPABASE_JWT_SECRET; Supabase access tokens are verified and mapped to a local user.
Every user-owned resource is additionally checked for ownership in the routers (spec §09/§13).
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, UserPreferences

DEV_TOKEN_TTL = timedelta(days=7)


def issue_dev_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "iss": "jtm-dev", "iat": now, "exp": now + DEV_TOKEN_TTL}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def get_or_create_user(db: Session, *, email: str | None = None, provider_id: str | None = None) -> User:
    stmt = select(User).where(User.provider_id == provider_id) if provider_id else select(User).where(User.email == email)
    user = db.scalar(stmt)
    if user:
        return user
    user = User(email=email, provider_id=provider_id)
    user.preferences = UserPreferences()
    db.add(user)
    db.commit()
    return user


def _decode(token: str, db: Session) -> User:
    s = get_settings()
    if s.supabase_jwt_secret:
        try:
            claims = jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return get_or_create_user(db, email=claims.get("email"), provider_id=claims["sub"])

    if s.app_env == "production":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth provider not configured")
    try:
        claims = jwt.decode(token, s.jwt_secret, algorithms=["HS256"], issuer="jtm-dev")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = db.get(User, claims["sub"])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return user


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return _decode(authorization.split(" ", 1)[1], db)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not expected or x_admin_token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
