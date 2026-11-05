"""Journal entries and fact-grounded AI drafts (spec F-07, F-08, §12)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.llm import LLMUnavailable, get_llm
from ..agent.prompts import JOURNAL_SYSTEM, MEMO_POLISH_SYSTEM
from ..models import JournalEntry, TrackingSession, Trip, UserPreferences
from .geo import KST, to_kst

DRAFT_LABEL = "AI 초안 — 저장 전 확인·수정해 주세요."


class NotEnoughRecords(ValueError):
    pass


def day_records(db: Session, trip: Trip, day: date) -> dict:
    memos = list(db.scalars(
        select(JournalEntry).where(JournalEntry.trip_id == trip.id, JournalEntry.date == day, JournalEntry.kind == "memo")
        .order_by(JournalEntry.captured_at)
    ))
    sessions = [s for s in trip.sessions if s.started_at and to_kst(s.started_at).date() == day] if trip.sessions else []
    return {"memos": memos, "sessions": sessions}


def _session_line(s: TrackingSession) -> str:
    km = (s.manual_distance_m if s.tracking_mode == "manual" and s.manual_distance_m is not None else s.distance_m) / 1000
    route = s.route.name if s.route else "코스 미지정"
    return f"- {route}, 기록된 이동 거리 {km:.1f}km"


def build_journal_input(records: dict, style: str | None) -> str:
    lines = ["[확인된 이동 기록]"]
    lines += [_session_line(s) for s in records["sessions"]] or ["- 없음"]
    lines.append("\n[사용자 기록] (시간순)")
    for m in records["memos"]:
        t = to_kst(m.captured_at).strftime("%H:%M") if m.captured_at else "시간 미상"
        body = (m.user_edited_text or m.text or "").strip()
        if body:
            lines.append(f"- {t} {'★' if m.is_highlight else ''}{body}")
        for media in m.media:
            if media.caption:
                lines.append(f"  · 사진 설명(사용자 작성): {media.caption}")
            elif media.media_type == "photo":
                lines.append("  · 사진 1장 (설명 없음 — 내용을 추측하지 말 것)")
    if style:
        lines.append(f"\n[문체 요청] {style}")
    return "\n".join(lines)


def _fallback_daily(records: dict) -> str:
    """Deterministic draft used when no LLM is available: only user words, no embellishment."""
    parts = []
    if records["sessions"]:
        parts.append("오늘의 걷기: " + ", ".join(_session_line(s)[2:] for s in records["sessions"]) + ".")
    for m in records["memos"]:
        body = (m.user_edited_text or m.text or "").strip()
        if body:
            parts.append(body)
    return "\n\n".join(parts)


def draft_daily(db: Session, trip: Trip, day: date, prefs: UserPreferences) -> JournalEntry:
    records = day_records(db, trip, day)
    has_text = any((m.user_edited_text or m.text or "").strip() or any(x.caption for x in m.media) for m in records["memos"])
    if not has_text and not records["sessions"]:
        raise NotEnoughRecords("이 날짜에는 기록이 없습니다. 메모나 음성 기록을 먼저 남겨 주세요.")
    llm = get_llm()
    if has_text and (not llm.external or prefs.consent_ai_processing):
        try:
            draft = llm.generate(JOURNAL_SYSTEM, build_journal_input(records, prefs.journal_style), max_tokens=3000)
        except LLMUnavailable:
            draft = _fallback_daily(records)
    else:
        draft = _fallback_daily(records)
        if not has_text:
            draft += "\n\n[추가 질문] 오늘 가장 기억에 남는 순간은 무엇이었나요?"

    entry = db.scalar(select(JournalEntry).where(JournalEntry.trip_id == trip.id, JournalEntry.date == day, JournalEntry.kind == "daily"))
    if entry is None:
        entry = JournalEntry(trip_id=trip.id, date=day, kind="daily")
        db.add(entry)
    entry.ai_draft = draft
    entry.status = "draft" if entry.status != "saved" else entry.status
    db.commit()
    return entry


def polish_memo(db: Session, entry: JournalEntry, prefs: UserPreferences) -> JournalEntry:
    if not (entry.text or "").strip():
        raise NotEnoughRecords("다듬을 메모 내용이 없습니다.")
    llm = get_llm()
    if llm.external and not prefs.consent_ai_processing:
        raise PermissionError("AI 처리 동의가 필요합니다.")
    try:
        entry.ai_draft = llm.generate(MEMO_POLISH_SYSTEM, entry.text, max_tokens=1500)
    except LLMUnavailable:
        entry.ai_draft = entry.text.strip()
    db.commit()
    return entry


def save_entry(db: Session, entry: JournalEntry, final_text: str, mood_tags: list[str] | None = None, is_highlight: bool | None = None) -> JournalEntry:
    entry.user_edited_text = final_text
    entry.status = "saved"
    if mood_tags is not None:
        entry.mood_tags = mood_tags
    if is_highlight is not None:
        entry.is_highlight = is_highlight
    db.commit()
    return entry


def entry_to_dict(e: JournalEntry, media_urls: dict[str, str] | None = None) -> dict:
    return {
        "id": e.id,
        "trip_id": e.trip_id,
        "date": e.date.isoformat(),
        "kind": e.kind,
        "text": e.text,
        "ai_draft": e.ai_draft,
        "ai_draft_label": DRAFT_LABEL if e.ai_draft else None,
        "user_edited_text": e.user_edited_text,
        "mood_tags": e.mood_tags or [],
        "is_highlight": e.is_highlight,
        "location": [e.lat, e.lon] if e.lat is not None else None,
        "captured_at": e.captured_at.isoformat() if e.captured_at else None,
        "status": e.status,
        "media": [
            {"id": m.id, "media_type": m.media_type, "caption": m.caption, "url": (media_urls or {}).get(m.id)}
            for m in e.media
        ],
    }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
