"""Single-orchestrator agent (spec §07):
Input → Context Builder → Intent & Policy → Tool Router → Verified Result → Response/Approval."""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..i18n import is_en, route_name, t
from ..models import AgentProposal, TrackingSession, Trip, User
from ..services.geo import now_kst
from ..services.route_service import route_freshness
from ..services.tracking_service import progress_summary
from .llm import LLMUnavailable, get_llm
from .prompts import COMPANION_SYSTEM
from .tools import TOOLS, ToolContext

EMERGENCY = re.compile(r"(살려|구조|119|응급|다쳤|부상|쓰러|길을 잃|조난|위급|사고"
                       r"|\b(help me|emergency|injur\w*|hurt|collaps\w*|accident|rescue|sos)\b|\bi(?:'m| am) lost\b)", re.I)
EMERGENCY_REPLY = (
    "위급한 상황이면 지금 바로 119(구조·구급)에 전화하세요. 경찰은 112입니다. "
    "가능하면 안전한 곳에 머물고, 주변 지형지물이나 코스 표식(간세·리본)을 알려 주세요. "
    "이 앱은 구조 서비스가 아니며, 화면의 현재 좌표를 구조대에 불러 줄 수 있습니다."
)
EMERGENCY_REPLY_EN = (
    "If this is an emergency, call 119 (rescue & ambulance) right now. Police is 112. "
    "If you can, stay somewhere safe and describe nearby landmarks or trail markers (ganse horse signs, ribbons). "
    "This app is not a rescue service; you can read the coordinates on screen to the rescuers."
)
MAX_HISTORY_TURNS = 6


def build_context(ctx: ToolContext) -> dict:
    """Minimal context only: no raw GPS log, no unrelated personal data (spec §07 메모리)."""
    p = ctx.prefs
    out: dict = {
        "now_kst": now_kst().strftime("%Y-%m-%d %H:%M (%a)"),
        "preferences": {
            "daily_distance_target_km": p.daily_distance_target_km,
            "pace_kmh": p.pace_kmh,
            "walk_end_time": p.walk_end_time,
            "rest_every_minutes": p.rest_every_minutes,
        },
        "location_sharing": bool(p.consent_location),
        "current_location_known": ctx._location() is not None,  # the position itself stays out of the prompt
    }
    if ctx.trip:
        out["trip"] = {"title": ctx.trip.title, "start": ctx.trip.start_date.isoformat(), "end": ctx.trip.end_date.isoformat()}
        sched = ctx.today_schedule()
        if sched:
            out["today_schedule"] = {
                "route": route_name(sched.route) if sched.route else None,
                "planned": f"{sched.planned_start}–{sched.planned_end}" if sched.planned_start else None,
                "note": sched.note,
            }
            if sched.route:
                out["today_route_data"] = route_freshness(sched.route)
    if ctx.session and ctx.session.status != "ended":
        summary = progress_summary(ctx.session, ctx.today_schedule())
        if not p.consent_location:
            summary.pop("last_position", None)
        out["active_session"] = summary
    return out


def _sources(calls) -> list[dict]:
    seen, out = set(), []
    for c in calls:
        if not c.ok or not isinstance(c.output, dict):
            continue
        src = c.output.get("source") or c.output.get("official_url") and t("제주올레 공식 사이트", "Official Jeju Olle site")
        url = c.output.get("source_url") or c.output.get("official_url")
        at = c.output.get("fetched_at") or c.output.get("notices_checked_at") or c.output.get("base_time")
        if src and (src, url) not in seen:
            seen.add((src, url))
            out.append({"name": src, "url": url, "checked_at": at})
    return out


def chat(db: Session, user: User, message: str, trip: Trip | None, session: TrackingSession | None,
         history: list[dict] | None = None, location: tuple[float, float] | None = None) -> dict:
    if EMERGENCY.search(message):
        return {"reply": t(EMERGENCY_REPLY, EMERGENCY_REPLY_EN), "intent": "emergency", "tool_calls": [], "proposals": [], "sources": [], "provider": "policy"}

    llm = get_llm()
    if llm.external and not user.preferences.consent_ai_processing:
        return {"reply": t("AI 대화를 사용하려면 설정에서 'AI 처리 동의'를 켜 주세요. 동의 전에는 대화 내용이 외부 AI로 전송되지 않습니다.",
                           "To chat with the AI, turn on 'AI processing' consent in Settings. Until then nothing is sent to an external AI."),
                "intent": "consent_required", "tool_calls": [], "proposals": [], "sources": [], "provider": "policy"}

    ctx = ToolContext(db=db, user=user, trip=trip, session=session, client_location=location)
    turns = (history or [])[-MAX_HISTORY_TURNS:]
    transcript = "\n".join(f"{'사용자' if t.get('role') == 'user' else '동행자'}: {str(t.get('text', ''))[:500]}" for t in turns)
    user_turn = (
        f"[현재 상황]\n{json.dumps(build_context(ctx), ensure_ascii=False, default=str)}\n\n"
        + (f"[최근 대화]\n{transcript}\n\n" if transcript else "")
        + ("[답변 언어]\nEnglish. The user is using the app in English: answer in natural, short English, following every "
           "other rule above. Translate Korean tool text (stories, notices) faithfully; keep Korean place names, adding "
           "a romanization or short English gloss in parentheses when it helps.\n\n" if is_en() else "")
        + f"[사용자 질문]\n{message}"
    )
    try:
        run = llm.run_tools(COMPANION_SYSTEM, user_turn, TOOLS, ctx.execute)
    except LLMUnavailable as exc:
        return {"reply": t(f"{exc} 저장된 일정과 기록은 계속 볼 수 있어요.", f"{exc} Your saved plan and records are still available."), "intent": "error", "tool_calls": [], "proposals": [], "sources": [], "provider": llm.name}

    proposal_ids = [c.output["proposal_id"] for c in run.tool_calls if c.ok and isinstance(c.output, dict) and "proposal_id" in c.output]
    proposals = [db.get(AgentProposal, pid) for pid in proposal_ids]
    return {
        "reply": run.text,
        "intent": "refused" if run.refused else "answer",
        "tool_calls": [{"name": c.name, "ok": c.ok} for c in run.tool_calls],
        "proposals": [{"id": p.id, "summary": p.summary, "payload": p.payload, "status": p.status} for p in proposals if p],
        "sources": _sources(run.tool_calls),
        "provider": run.provider,
    }
