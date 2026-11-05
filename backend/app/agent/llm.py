"""LLM provider interface. The rest of the app depends only on `LLM`, so the provider can be
swapped (spec §08 '제공사 종속성을 줄이기 위해 내부 인터페이스로 감싼다')."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import get_settings

log = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict], Any]


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    name: str
    input: dict
    ok: bool
    output: Any = None


@dataclass
class AgentRun:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    refused: bool = False
    provider: str = ""


class LLM(Protocol):
    name: str
    external: bool  # True when user data leaves our servers (requires AI-processing consent)

    def generate(self, system: str, user: str, max_tokens: int = 4000) -> str: ...

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], execute: ToolExecutor, max_turns: int = 6) -> AgentRun: ...


class AnthropicLLM:
    name = "anthropic"
    external = True
    BETAS = ["server-side-fallback-2026-07-01"]

    def __init__(self):
        import anthropic

        s = get_settings()
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key) if s.anthropic_api_key else anthropic.Anthropic()
        self.model = s.llm_model
        self.effort = s.llm_effort

    def _create(self, **kwargs):
        try:
            return self.client.beta.messages.create(
                model=self.model,
                output_config={"effort": self.effort},
                betas=self.BETAS,
                fallbacks="default",  # server-side retry on Anthropic's recommended model if a classifier declines
                **kwargs,
            )
        except self._anthropic.RateLimitError as e:
            raise LLMUnavailable("AI 요청이 많아 잠시 후 다시 시도해 주세요.") from e
        except self._anthropic.APIStatusError as e:
            log.warning("anthropic status error %s request_id=%s", e.status_code, getattr(e, "request_id", None))
            raise LLMUnavailable("AI 서버 오류가 발생했습니다.") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMUnavailable("AI 서버에 연결할 수 없습니다. 네트워크를 확인하세요.") from e

    @staticmethod
    def _text(resp) -> str:
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def generate(self, system: str, user: str, max_tokens: int = 4000) -> str:
        resp = self._create(max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
        if resp.stop_reason == "refusal":
            raise LLMUnavailable("AI가 이 요청을 처리하지 않았습니다.")
        return self._text(resp)

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], execute: ToolExecutor, max_turns: int = 6) -> AgentRun:
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        messages: list[dict] = [{"role": "user", "content": user}]
        calls: list[ToolCall] = []
        for _ in range(max_turns):
            resp = self._create(max_tokens=16000, system=system, tools=tool_defs, messages=messages)
            if resp.stop_reason == "refusal":
                return AgentRun(text="이 요청은 도와드리기 어렵습니다. 다른 질문을 해 주세요.", tool_calls=calls, refused=True, provider=self.name)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                return AgentRun(text=self._text(resp), tool_calls=calls, provider=self.name)
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                try:
                    out = execute(tu.name, dict(tu.input))
                    calls.append(ToolCall(tu.name, dict(tu.input), True, out))
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out, ensure_ascii=False, default=str)})
                except Exception as exc:  # tool errors go back to the model, never crash the turn
                    calls.append(ToolCall(tu.name, dict(tu.input), False, str(exc)))
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": f"Error: {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
        return AgentRun(text="요청을 처리하는 데 단계가 너무 많이 필요합니다. 질문을 나눠서 다시 해 주세요.", tool_calls=calls, provider=self.name)


class MockLLM:
    """Offline provider for development/tests. It never writes free text about the world:
    it routes by keyword to the same tools and reports their verified output."""

    name = "mock"
    external = False

    INTENTS = [
        (("날씨", "비", "바람", "예보"), "get_weather", {}),
        (("공지", "우회", "통제", "폐쇄"), "get_route_notices", {}),
        (("도착", "남은", "언제", "몇 시"), "estimate_arrival", {}),
        (("다음 경유지", "다음 포인트", "경유지까지"), "get_next_waypoint", {}),
        (("경유지",), "get_waypoints", {}),
        (("카페",), "find_places_near", {"category": "cafe"}),
        (("식당", "맛집", "밥", "점심", "저녁"), "find_places_near", {"category": "food"}),
        (("쉬어", "쉼터", "휴식"), "find_places_near", {"category": "rest"}),
        (("화장실",), "find_places_near", {"category": "toilet"}),
        (("일기",), "create_journal_draft", {}),
        (("일정", "계획"), "get_trip_plan", {}),
        (("코스",), "list_routes", {}),
    ]

    def generate(self, system: str, user: str, max_tokens: int = 4000) -> str:
        raise LLMUnavailable("mock provider has no free-text generation")

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], execute: ToolExecutor, max_turns: int = 6) -> AgentRun:
        question = user.rsplit("[사용자 질문]", 1)[-1]
        available = {t.name for t in tools}
        for keywords, tool, args in self.INTENTS:
            if tool in available and any(k in question for k in keywords):
                try:
                    out = execute(tool, dict(args))
                    return AgentRun(text=_summarize(tool, out), tool_calls=[ToolCall(tool, args, True, out)], provider=self.name)
                except Exception as exc:
                    return AgentRun(text=f"정보를 가져오지 못했습니다: {exc}", tool_calls=[ToolCall(tool, args, False, str(exc))], provider=self.name)
        return AgentRun(
            text="(오프라인 모드) 코스·일정·날씨·공지·도착 예상·주변 장소·일기 초안에 대해 물어보실 수 있어요. "
                 "자유 대화는 AI 키를 설정하면 사용할 수 있습니다.",
            provider=self.name,
        )


def _summarize(tool: str, out: Any) -> str:
    if isinstance(out, dict):
        if out.get("status") == "unavailable":
            return out.get("message") or "정보를 확인할 수 없습니다."
        if tool == "estimate_arrival" and "eta_hhmm" in out:
            return f"남은 거리 약 {out['remaining_km']}km, 예상 도착 {out['eta_hhmm']} (일몰 {out['sunset_hhmm']}). {out['assumptions']}"
        if tool == "get_route_notices":
            notices = out.get("notices", [])
            head = f"{out.get('route')} 활성 공지 {len(notices)}건." + "".join(f"\n- {n['title']}" for n in notices)
            return head + (f"\n{out['caveat']}" if out.get("caveat") else "")
        if tool == "get_weather" and out.get("forecast"):
            f = out["forecast"][0]
            return f"{f['time'][11:16]} 기준 {f['sky']}, {f['temp_c']}°C, 강수확률 {f['precip_prob_pct']}% (출처: {out['source']}, 발표 {out['base_time']})"
        if tool == "find_places_near":
            places = out.get("places", [])
            if not places:
                return out.get("message") or "주변에서 확인된 장소가 없습니다."
            src = ", ".join(f"{s['name']}({s['ref_date'] or '기준일 미상'})" for s in out.get("sources", []))
            return "\n".join(f"- {p['name']} ({p['distance_m']}m)" for p in places[:5]) + f"\n{out['caveat']} 출처: {src}"
        if tool == "get_next_waypoint" and out.get("next"):
            n = out["next"]
            dist = f" 약 {n['distance_m']}m 남았어요." if n.get("distance_m") is not None else ""
            seen = f" 지나온 곳: {', '.join(out['visited'])}." if out.get("visited") else ""
            return f"다음 경유지는 {n['name']}입니다.{dist}{seen}"
        if tool == "get_waypoints" and out.get("waypoints"):
            names = " → ".join(w["name"] for w in out["waypoints"])
            geo = "" if out.get("has_geometry") else " (경로 좌표 적재 전)"
            return f"{out['route']} 경유지: {names}{geo}"
        if "message" in out:
            return str(out["message"])
    return json.dumps(out, ensure_ascii=False, default=str)[:1500]


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        s = get_settings()
        use_anthropic = s.llm_provider == "anthropic" or (s.llm_provider == "auto" and bool(s.anthropic_api_key))
        _llm = AnthropicLLM() if use_anthropic else MockLLM()
    return _llm


def set_llm(llm: LLM | None) -> None:
    """Test hook."""
    global _llm
    _llm = llm
