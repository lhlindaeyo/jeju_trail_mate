"""Private object storage. Local filesystem in development; swap `LocalStorage` for a
Supabase Storage / S3 implementation with the same interface in production.
Files are never publicly listed; access goes through short-lived HMAC-signed URLs."""
import hashlib
import hmac
import time
import uuid
from pathlib import Path

from ..config import get_settings

ALLOWED_TYPES = {
    "image/jpeg": ("photo", ".jpg"), "image/png": ("photo", ".png"), "image/webp": ("photo", ".webp"),
    "image/heic": ("photo", ".heic"), "audio/webm": ("audio", ".webm"), "audio/mpeg": ("audio", ".mp3"),
    "audio/mp4": ("audio", ".m4a"), "audio/ogg": ("audio", ".ogg"), "audio/wav": ("audio", ".wav"),
    "application/pdf": ("pdf", ".pdf"), "text/html": ("html", ".html"),
}


class LocalStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or get_settings().storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, owner_id: str, data: bytes, content_type: str) -> str:
        _kind, ext = ALLOWED_TYPES[content_type]
        key = f"{owner_id}/{uuid.uuid4().hex}{ext}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def put_named(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def delete_prefix(self, owner_id: str) -> int:
        d = self._path(owner_id)
        n = 0
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
                    n += 1
        return n


def sign(key: str, ttl: int | None = None) -> dict:
    s = get_settings()
    exp = int(time.time()) + (ttl or s.signed_url_ttl_seconds)
    sig = hmac.new(s.signing_secret.encode(), f"{key}:{exp}".encode(), hashlib.sha256).hexdigest()
    return {"key": key, "exp": exp, "sig": sig}


def verify(key: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    expected = hmac.new(get_settings().signing_secret.encode(), f"{key}:{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def signed_url(key: str) -> str:
    t = sign(key)
    return f"/v1/files?key={t['key']}&exp={t['exp']}&sig={t['sig']}"


storage = LocalStorage()
