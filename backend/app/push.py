"""Web Push: alerts on the phone's top banner and lock screen, even with the app closed.

The browser subscribes with our VAPID public key (Settings → 폰 알림 켜기) and gives us an endpoint at its push
service (FCM for Chrome/Android, Apple for an iPhone home-screen app, Mozilla for Firefox). For every alert the
rules engine creates, we send an encrypted message there; the service worker turns it into a system notification.

Needs `pywebpush` (requirements.txt) and a key pair in backend/.env:

    python -m app.push keygen       # prints VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY lines to paste into .env

Without them push is simply off (/v1/push/config says why) and alerts still pop up while the app is open."""
from __future__ import annotations

import base64
import json
import logging
import sys
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Notification, PushSubscription

log = logging.getLogger(__name__)
MAX_FAILURES = 3  # drop a subscription after this many failed sends in a row (uninstalled app, revoked permission)


def library_available() -> bool:
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    s = get_settings()
    if not library_available():
        return {"enabled": False, "public_key": None, "reason": "서버에 pywebpush가 설치되지 않았어요"}
    if not (s.vapid_public_key and s.vapid_private_key):
        return {"enabled": False, "public_key": None, "reason": "서버에 VAPID 키가 없어요 (python -m app.push keygen)"}
    return {"enabled": True, "public_key": s.vapid_public_key, "reason": None}


def payload(n: Notification) -> dict:
    """What the service worker shows. Safety alerts stay on screen until tapped."""
    return {"id": n.id, "title": n.title, "body": n.message, "severity": n.severity, "event_type": n.event_type,
            "url": "/#brief"}


def _send_one(sub: PushSubscription, data: dict) -> int | None:
    """Returns None on success, else the HTTP status (or 0 for a network error)."""
    from pywebpush import WebPushException, webpush

    s = get_settings()
    try:
        webpush(subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=json.dumps(data, ensure_ascii=False), vapid_private_key=s.vapid_private_key,
                vapid_claims={"sub": s.vapid_subject}, ttl=6 * 3600,
                headers={"Urgency": "high" if data.get("severity") == "safety" else "normal"})
        return None
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) or 0
    except Exception:  # network
        return 0


def send(db: Session, user_id: str, data: dict) -> dict:
    subs = list(db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))
    if not subs or not status()["enabled"]:
        return {"sent": 0, "failed": 0, "subscriptions": len(subs)}
    sent = failed = 0
    now = datetime.now(timezone.utc)
    for sub in subs:
        code = _send_one(sub, data)
        if code is None:
            sent += 1
            sub.last_ok_at, sub.failures = now, 0
            continue
        failed += 1
        sub.failures = (sub.failures or 0) + 1
        if code in (404, 410) or sub.failures >= MAX_FAILURES:  # gone for good
            db.delete(sub)
    db.commit()
    return {"sent": sent, "failed": failed, "subscriptions": len(subs)}


def deliver_async(notifications: list[Notification]) -> None:
    """Fire-and-forget push for alerts just created, so a slow push service never delays the GPS upload."""
    if not notifications or not status()["enabled"]:
        return
    jobs = [(n.user_id, payload(n)) for n in notifications]

    def run():
        from .db import SessionLocal

        with SessionLocal() as db:
            for uid, data in jobs:
                try:
                    send(db, uid, data)
                except Exception:
                    log.exception("push send failed")

    threading.Thread(target=run, daemon=True).start()


def keygen() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    raw_priv = key.private_numbers().private_value.to_bytes(32, "big")
    raw_pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return b64(raw_pub), b64(raw_priv)


if __name__ == "__main__":
    if sys.argv[1:] == ["keygen"]:
        pub, priv = keygen()
        print(f"VAPID_PUBLIC_KEY={pub}\nVAPID_PRIVATE_KEY={priv}")
    else:
        print(__doc__)
