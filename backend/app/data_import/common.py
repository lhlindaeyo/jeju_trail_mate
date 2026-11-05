"""Shared helpers for the data pipeline (v1.2 §05): register → validate → transform → load."""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import DataSource


class ImportError_(RuntimeError):
    """Validation failure with a human-readable cause."""


def sha256(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def register(db: Session, source_id: str, files: list[Path], *, license: str | None = None,
             ref_date: str | None = None, note: str | None = None) -> DataSource:
    src = db.get(DataSource, source_id)
    if src is None:
        raise ImportError_(f"unknown data source {source_id}; seed the database first")
    src.downloaded_at = datetime.fromtimestamp(max(p.stat().st_mtime for p in files), tz=timezone.utc)
    src.file_hash = sha256(files)
    src.status = "imported"
    if license:
        src.license = license
    if ref_date:
        src.ref_date = ref_date
    if note:
        src.note = note
    return src


def read_csv(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_(f"{path.name}: 인코딩을 판별하지 못했습니다(UTF-8/CP949 아님)")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ImportError_(f"{path.name}: 데이터 행이 없습니다")
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def pick(columns: list[str], *needles: str) -> str | None:
    """First column whose name contains any needle (in priority order)."""
    for n in needles:
        for c in columns:
            if n in c.replace(" ", ""):
                return c
    return None
