"""Journey book (spec F-09, §12): HTML template → (optional) PDF.
Only saved, user-approved entries are included; empty days stay empty."""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from ..models import JourneyBook, MediaAsset, Trip
from .geo import KST, now_kst, to_kst
from .journal_service import passed_places
from .storage import storage
from .tracking_service import route_progress

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
THEMES = {
    "olle_green": {"accent": "#315d49", "paper": "#fbfaf5", "ink": "#23352d"},
    "sunset": {"accent": "#c66c45", "paper": "#fff8f1", "ink": "#3a2a22"},
    "ocean": {"accent": "#2f6177", "paper": "#f5f9fa", "ink": "#1f3440"},
}
COMPLETION_RATIO = 0.8


def _data_uri(m: MediaAsset) -> str | None:
    try:
        return f"data:{m.content_type};base64,{base64.b64encode(storage.read(m.storage_key)).decode()}"
    except (OSError, ValueError):
        return None


def _session_km(s) -> float:
    d = s.manual_distance_m if s.tracking_mode == "manual" and s.manual_distance_m is not None else s.distance_m
    return d / 1000


def _thin(pts: list, n: int = 400) -> list:
    step = max(1, len(pts) // n)
    return pts[::step] + [pts[-1]]


def _track_svg(trip: Trip, width: int = 640, height: int = 360) -> str | None:
    """Official Hayoung path (dashed) + recorded GPS tracks (solid) as SVG — no map tiles in the PDF."""
    tracks, routes = [], {}
    for s in trip.sessions:
        pts = [(p.lat, p.lon) for p in sorted(s.points, key=lambda p: p.timestamp)]
        if len(pts) >= 2:
            tracks.append(_thin(pts))
        if s.route and s.route.has_geometry and s.route.path:
            routes[s.route.id] = _thin([tuple(p) for p in s.route.path])
    if not tracks and not routes:
        return None
    every = [p for t in [*tracks, *routes.values()] for p in t]
    lats = [p[0] for p in every]
    lons = [p[1] for p in every]
    min_lat, max_lat, min_lon, max_lon = min(lats), max(lats), min(lons), max(lons)
    span = max(max_lat - min_lat, (max_lon - min_lon) * 0.84, 1e-4)
    pad = 20

    def xy(lat, lon):
        x = pad + (lon - min_lon) * 0.84 / span * (width - 2 * pad)
        y = height - pad - (lat - min_lat) / span * (height - 2 * pad)
        return f"{x:.1f},{y:.1f}"

    official = "".join(f'<polyline class="official" points="{" ".join(xy(*p) for p in t)}" />' for t in routes.values())
    lines = official + "".join(f'<polyline points="{" ".join(xy(*p) for p in t)}" />' for t in tracks)
    return f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" class="track">{lines}</svg>'


def assemble(db: Session, trip: Trip, book: JourneyBook) -> dict:
    excluded = set(book.excluded_entry_ids or [])
    chapters = []
    day = trip.start_date
    total_km = 0.0
    total_min = 0.0
    completed = {"jeju_olle": set(), "hayoung_olle": set()}
    for s in trip.sessions:
        if s.ended_at:
            total_km += _session_km(s)
            total_min += max((s.ended_at - s.started_at).total_seconds() - s.paused_seconds, 0) / 60
            if session_completed(s):
                completed[s.route.trail_type].add(s.route.name)
    record_days = 0
    while day <= trip.end_date:
        entries = [e for e in trip.journal_entries if e.date == day and e.status == "saved" and e.id not in excluded]
        daily = next((e for e in entries if e.kind == "daily"), None)
        memos = [e for e in entries if e.kind == "memo"]
        photos = []
        for e in entries:
            for m in e.media:
                if m.media_type == "photo" and m.consent_state == "granted":
                    uri = _data_uri(m)
                    if uri:
                        photos.append({"src": uri, "caption": m.caption})
        sessions = [s for s in trip.sessions if to_kst(s.started_at).date() == day and s.ended_at]
        if entries or sessions:
            record_days += 1
        chapters.append({
            "date": day,
            "weekday": "월화수목금토일"[day.weekday()],
            "daily_text": (daily.user_edited_text or daily.text) if daily else None,
            "memos": [{"text": m.user_edited_text or m.text, "highlight": m.is_highlight,
                       "time": to_kst(m.captured_at).strftime("%H:%M")} for m in memos if (m.user_edited_text or m.text)],
            "photos": photos[:6],
            "walks": [{"route": s.route.name if s.route else "코스 미지정", "km": round(_session_km(s), 1)} for s in sessions],
            "passed": passed_places(sessions),
        })
        day += timedelta(days=1)
    highlights = [
        {"date": e.date, "text": e.user_edited_text or e.text}
        for e in trip.journal_entries
        if e.is_highlight and e.status == "saved" and e.id not in excluded and (e.user_edited_text or e.text)
    ]
    cover = None
    if book.cover_media_id:
        m = db.get(MediaAsset, book.cover_media_id)
        if m and m.entry.trip_id == trip.id:
            cover = _data_uri(m)
    return {
        "book": book,
        "trip": trip,
        "theme": THEMES.get(book.theme, THEMES["olle_green"]),
        "cover": cover,
        "chapters": chapters,
        "highlights": highlights,
        "track_svg": _track_svg(trip),
        "stats": {
            "total_km": round(total_km, 1),
            "total_hours": round(total_min / 60, 1),
            "record_days": record_days,
            "trip_days": (trip.end_date - trip.start_date).days + 1,
            "completed_olle": sorted(completed["jeju_olle"]),
            "completed_hayoung": sorted(completed["hayoung_olle"]),
        },
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
    }


def session_completed(s) -> bool:
    if not s.ended_at or not s.route:
        return False
    ratio = route_progress(s)["ratio"]
    return ratio is not None and ratio >= COMPLETION_RATIO


def eligibility(db: Session, trip: Trip, today=None) -> dict:
    """Book download unlocks on the trip's last day once the planned courses are completed (v1.2 UI spec)."""
    from .schedule_service import approved_for

    today = today or now_kst().date()
    planned: dict[str, str] = {}
    d = trip.start_date
    while d <= trip.end_date:
        sched = approved_for(db, trip, d)
        if sched and sched.route:
            planned[sched.route_id] = sched.route.name
            for rid in sched.extra_route_ids or []:
                planned[rid] = rid
        d += timedelta(days=1)
    done = {s.route_id: s.route.name for s in trip.sessions if session_completed(s)}
    for rid in list(planned):
        if planned[rid] == rid and rid in done:
            planned[rid] = done[rid]
    missing = [name for rid, name in planned.items() if rid not in done]
    entries = sum(1 for e in trip.journal_entries if e.status == "saved")
    reasons = []
    if today < trip.end_date:
        reasons.append(f"여행 마지막 날({trip.end_date.isoformat()})부터 만들 수 있어요.")
    if planned and missing:
        reasons.append("아직 완주하지 않은 코스: " + ", ".join(missing))
    if not planned and not done:
        reasons.append("완주한 코스 기록이 없어요.")
    if entries == 0:
        reasons.append("저장한 일기가 없어요. 일기를 한 편 이상 저장해 주세요.")
    return {"eligible": not reasons, "reasons": reasons, "completed": sorted(done.values()), "missing": missing,
            "saved_entries": entries, "last_day": trip.end_date.isoformat(),
            "rule": f"코스 진행률 {int(COMPLETION_RATIO * 100)}% 이상으로 세션을 마치면 앱 기록상 완주로 봅니다(공식 인증 아님)."}


def generate(db: Session, trip: Trip, book: JourneyBook) -> JourneyBook:
    book.status, book.error = "generating", None
    db.commit()
    try:
        html = _env.get_template("book.html").render(**assemble(db, trip, book))
        book.storage_key = storage.put_named(f"{trip.user_id}/books/{book.id}.html", html.encode("utf-8"))
        book.status, book.generated_at = "preview_ready", datetime.now(timezone.utc)
        book.approved_at = None
    except Exception as exc:  # surface the failure, allow retry (spec §12)
        log.exception("book generation failed")
        book.status, book.error = "failed", f"책 생성 중 오류가 발생했습니다: {type(exc).__name__}"
    db.commit()
    return book


def export_pdf(book: JourneyBook) -> bytes | None:
    """Render PDF with WeasyPrint when installed (needs system Pango/GTK libs). Returns None
    otherwise; the client then uses the browser's print-to-PDF on the HTML preview."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        return None
    html = storage.read(book.storage_key).decode("utf-8")
    return HTML(string=html).write_pdf()
