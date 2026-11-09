"""LLM provider interface. The rest of the app depends only on `LLM`, so the provider can be
swapped (spec §08 '제공사 종속성을 줄이기 위해 내부 인터페이스로 감싼다')."""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

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


def match_intent(user: str, available: set[str]) -> tuple[str, dict] | None:
    """Keyword → (tool, args) for the question part of the prompt, or None."""
    question = user.rsplit("[사용자 질문]", 1)[-1]
    for keywords, tool, args in MockLLM.INTENTS:
        if tool in available and any(k in question for k in keywords):
            return tool, dict(args)
    return None


class MockLLM:
    """Offline provider for development/tests. It never writes free text about the world:
    it routes by keyword to the same tools and reports their verified output."""

    name = "mock"
    external = False

    INTENTS = [
        (("이야기", "전설", "유래", "설화"), "get_tour_story", {}),
        (("관광지", "명소", "가볼", "볼거리"), "find_tour_spots", {}),
        (("날씨", "비", "바람", "예보"), "get_weather", {}),
        (("공지", "우회", "통제", "폐쇄"), "get_route_notices", {}),
        (("브리핑", "앞길", "걸어도", "해 지기", "해지기", "일몰"), "get_trail_brief", {}),
        (("도착", "남은", "언제", "몇 시"), "estimate_arrival", {}),
        (("다음 경유지", "다음 포인트", "경유지까지"), "get_next_waypoint", {}),
        (("경유지",), "get_waypoints", {}),
        (("카페",), "find_places_near", {"category": "cafe"}),
        (("식당", "맛집", "밥", "점심", "저녁"), "find_places_near", {"category": "food"}),
        (("쉬어", "쉼터", "휴식"), "find_places_near", {"category": "rest"}),
        (("화장실",), "find_places_near", {"category": "toilet"}),
        (("숙소", "숙박", "게스트하우스"), "find_places_near", {"category": "lodging"}),
        (("와이파이", "wifi"), "find_places_near", {"category": "wifi"}),
        (("일정", "계획"), "get_trip_plan", {}),
        (("코스",), "list_routes", {}),
    ]

    def generate(self, system: str, user: str, max_tokens: int = 4000) -> str:
        raise LLMUnavailable("mock provider has no free-text generation")

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], execute: ToolExecutor, max_turns: int = 6) -> AgentRun:
        hit = match_intent(user, {t.name for t in tools})
        if hit:
            tool, args = hit
            try:
                out = execute(tool, dict(args))
                return AgentRun(text=_summarize(tool, out), tool_calls=[ToolCall(tool, args, True, out)], provider=self.name)
            except Exception as exc:
                return AgentRun(text=f"정보를 가져오지 못했습니다: {exc}", tool_calls=[ToolCall(tool, args, False, str(exc))], provider=self.name)
        return AgentRun(
            text="(오프라인 모드) 코스·일정·날씨·공지·도착 예상·주변 장소·앞길 브리핑에 대해 물어보실 수 있어요. "
                 "자유 대화는 AI 키를 설정하면 사용할 수 있습니다.",
            provider=self.name,
        )


def _summarize(tool: str, out: Any) -> str:
    if isinstance(out, dict):
        if out.get("status") == "unavailable":
            return out.get("message") or "정보를 확인할 수 없습니다."
        if tool == "estimate_arrival" and "eta_hhmm" in out:
            return f"남은 거리 약 {out['remaining_km']}km, 예상 도착 {out['eta_hhmm']} (일몰 {out['sunset_hhmm']}). {out['assumptions']}"
        if tool == "get_trail_brief":
            lines = [out["daylight"].get("message") or "일몰 비교 정보를 확인할 수 없습니다."]
            sup = out["supplies"]
            if sup.get("status") == "ok":
                first = next(((g["label"], g["places"][0]) for g in sup["groups"] if g["places"]), None)
                if first:
                    p = first[1]
                    where = f"앞 {p['ahead_m']}m" if p.get("ahead_m") is not None else f"{p['distance_m']}m"
                    lines.append(f"가까운 {first[0]}: {p['name']} ({where}, 영업 여부는 확인 필요)")
            w = out["weather"]
            lines.append(w.get("headline") or w.get("message") or "날씨를 확인하지 못했습니다.")
            return "\n".join(lines)
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

            def line(p: dict) -> str:
                where = f"{p['distance_m']}m" if p.get("distance_m") is not None else (p.get("address") or "주소 미상")
                tag = f" [{p['badge']}]" if p.get("badge") else ""
                # detail = "메뉴 가격, 메뉴 가격 · 휴무" — show the first three menu items, never cut mid-price.
                menu = (p.get("detail") or "").split(" · ")[0].strip(", ")
                items = re.split(r",\s+", menu)
                extra = f" · {', '.join(items[:3])}" if menu else ""
                phone = f" · ☎ {p['phone']}" if p.get("phone") else ""
                return f"- {p['name']}{tag} ({where}){extra}{phone}"

            head = f"{out['message']}\n" if out.get("message") else ""
            return head + "\n".join(line(p) for p in places[:5]) + f"\n{out['caveat']} 출처: {src}"
        if tool == "find_tour_spots" and out.get("places"):
            head = f"{out['note']}\n" if out.get("note") else ""
            lines = []
            for p in out["places"]:
                dist = f" ({p['distance_m']}m)" if p.get("distance_m") is not None else ""
                lines.append(f"- {p['name']}{dist} · {p['summary']}")
            return head + "\n".join(lines) + f"\n{out['caveat']} 출처: {out['source']}"
        if tool == "get_tour_story" and out.get("stories"):
            body = "\n\n".join(f"[{s['name']}]\n{s['story']}" for s in out["stories"])
            return f"{body}\n\n{out['caveat']} 출처: {out['source']}"
        if tool == "get_tour_story" and out.get("status") == "not_found":
            return f"{out['message']} 정리된 곳: {', '.join(out['available'])}"
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


# --- OpenAI-compatible providers (Ollama, Gemini, Groq, OpenRouter, vLLM, llama.cpp, LM Studio ...) ---------
def is_local_url(url: str) -> bool:
    """True when the endpoint is on this machine / LAN / a docker service name (data does not leave)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".localhost") or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


class _KeyRing:
    """Comma-separated API keys, used round-robin. A key rejected with 401/403/429 rests for COOLDOWN_S."""

    COOLDOWN_S = 60.0

    def __init__(self, keys: str | None):
        self.keys = [k.strip() for k in (keys or "").split(",") if k.strip()]
        self.i = 0
        self.resting: dict[str, float] = {}

    def current(self) -> str | None:
        if not self.keys:
            return None
        now = time.monotonic()
        for off in range(len(self.keys)):
            idx = (self.i + off) % len(self.keys)
            if self.resting.get(self.keys[idx], 0.0) <= now:
                self.i = idx
                return self.keys[idx]
        return self.keys[self.i % len(self.keys)]  # all resting: try anyway

    def advance(self) -> None:
        if len(self.keys) > 1:
            self.i = (self.i + 1) % len(self.keys)

    def reject(self) -> None:
        key = self.current()
        if key:
            self.resting[key] = time.monotonic() + self.COOLDOWN_S
        self.advance()


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _clean_text(text: str | None) -> str:
    """Drop reasoning blocks some local models (Qwen3, DeepSeek-R1) put in the visible content."""
    out = _THINK.sub("", text or "")
    return out.split("<think>")[0].strip()  # an unclosed block means the answer was cut off


class OpenAICompatLLM:
    def __init__(self, name: str, base_url: str, model: str, api_key: str | None = None, *,
                 external: bool | None = None, timeout: float = 120.0, max_turns: int = 4,
                 client: httpx.Client | None = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keys = _KeyRing(api_key)
        self.external = (not is_local_url(base_url)) if external is None else external
        self.max_turns = max_turns
        self.client = client or httpx.Client(timeout=timeout)

    def _post(self, payload: dict) -> dict:
        attempts = max(1, len(self.keys.keys))
        for n in range(attempts):
            headers = {"Content-Type": "application/json"}
            if self.keys.current():
                headers["Authorization"] = f"Bearer {self.keys.current()}"
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMUnavailable("AI 응답이 너무 오래 걸립니다. 잠시 후 다시 시도해 주세요.") from e
            except httpx.HTTPError as e:
                raise LLMUnavailable("AI 서버에 연결할 수 없습니다. 네트워크를 확인하세요.") from e
            if r.status_code in (401, 403, 429) and n + 1 < attempts:
                log.warning("%s: key #%d rejected (%s), trying the next key", self.name, self.keys.i, r.status_code)
                self.keys.reject()
                continue
            if r.status_code == 429:
                raise LLMUnavailable("AI 요청이 많아 잠시 후 다시 시도해 주세요.")
            if r.status_code >= 400:
                log.warning("%s status error %s: %s", self.name, r.status_code, r.text[:200])
                raise LLMUnavailable("AI 서버 오류가 발생했습니다.")
            self.keys.advance()
            try:
                return r.json()
            except ValueError as e:
                raise LLMUnavailable("AI 서버 응답을 해석할 수 없습니다.") from e
        raise LLMUnavailable("AI 서버 오류가 발생했습니다.")  # pragma: no cover

    @staticmethod
    def _message(data: dict) -> dict:
        return (data.get("choices") or [{}])[0].get("message") or {}

    def generate(self, system: str, user: str, max_tokens: int = 4000) -> str:
        data = self._post({"model": self.model, "max_tokens": max_tokens, "temperature": 0.4,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        text = _clean_text(self._message(data).get("content"))
        if not text:
            raise LLMUnavailable("AI가 빈 응답을 반환했습니다.")
        return text

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], execute: ToolExecutor, max_turns: int = 6) -> AgentRun:
        tool_defs = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        calls: list[ToolCall] = []
        grounded = False

        def run_one(call_id: str, name: str, args: dict) -> None:
            try:
                out = execute(name, args)
                calls.append(ToolCall(name, args, True, out))
                content = json.dumps(out, ensure_ascii=False, default=str)
            except Exception as exc:  # tool errors go back to the model, never crash the turn
                calls.append(ToolCall(name, args, False, str(exc)))
                content = f"Error: {exc}"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

        for _ in range(min(max_turns, self.max_turns)):
            data = self._post({"model": self.model, "max_tokens": 2000, "temperature": 0.2, "messages": messages, "tools": tool_defs})
            msg = self._message(data)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                hint = None if (calls or grounded) else match_intent(user, {t.name for t in tools})
                if hint:
                    # Small models often answer from memory instead of calling a tool. When the question
                    # clearly maps to one, fetch the verified data ourselves and let the model phrase it.
                    grounded = True
                    name, args = hint
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_grounding", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
                    run_one("call_grounding", name, args)
                    continue
                text = _clean_text(msg.get("content"))
                if not text:
                    raise LLMUnavailable("AI가 빈 응답을 반환했습니다.")
                return AgentRun(text=text, tool_calls=calls, provider=self.name)
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                name, raw = fn.get("name") or "", fn.get("arguments") or {}
                call_id = tc.get("id") or f"call_{i}"
                try:
                    args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                except ValueError as exc:
                    calls.append(ToolCall(name, {}, False, f"bad arguments: {exc}"))
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": f"Error: bad arguments: {exc}"})
                    continue
                run_one(call_id, name, args)
        return AgentRun(text="요청을 처리하는 데 단계가 너무 많이 필요합니다. 질문을 나눠서 다시 해 주세요.", tool_calls=calls, provider=self.name)


class FallbackLLM:
    """Tries each provider in order; the next one is used when one is unavailable (rate limit, offline, ...).
    `external` is True if ANY member is external: a later fallback may send the data out, so consent applies."""

    def __init__(self, members: list[LLM]):
        self.members = members
        self.name = ">".join(m.name for m in members)
        self.external = any(m.external for m in members)

    def _first(self, call: Callable[[LLM], Any]) -> Any:
        last: LLMUnavailable | None = None
        for m in self.members:
            try:
                return call(m)
            except LLMUnavailable as exc:
                log.warning("LLM provider %s unavailable (%s); trying next", m.name, exc)
                last = exc
        raise last or LLMUnavailable("사용 가능한 AI 제공사가 없습니다.")

    def generate(self, system: str, user: str, max_tokens: int = 4000) -> str:
        return self._first(lambda m: m.generate(system, user, max_tokens))

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], execute: ToolExecutor, max_turns: int = 6) -> AgentRun:
        return self._first(lambda m: m.run_tools(system, user, tools, execute, max_turns))


def _make_provider(name: str) -> LLM | None:
    """One chain entry -> provider, or None when it is not configured (missing key / URL)."""
    s = get_settings()
    kw = {"timeout": s.llm_timeout_s, "max_turns": s.llm_max_turns}
    if name == "mock":
        return MockLLM()
    if name == "anthropic":
        return AnthropicLLM() if s.anthropic_api_key else None
    if name == "ollama":
        return OpenAICompatLLM("ollama", s.ollama_base_url, s.ollama_model, None, **kw)
    if name == "openai":
        return OpenAICompatLLM("openai", s.openai_base_url, s.openai_model, s.openai_api_key, **kw) if s.openai_base_url and s.openai_model else None
    if name in ("gemini", "groq", "openrouter"):
        key = getattr(s, f"{name}_api_key")
        return OpenAICompatLLM(name, getattr(s, f"{name}_base_url"), getattr(s, f"{name}_model"), key, **kw) if key else None
    log.warning("unknown LLM_CHAIN entry %r ignored", name)
    return None


def build_llm() -> LLM:
    s = get_settings()
    if s.llm_chain.strip():
        members = [m for m in (_make_provider(n.strip().lower()) for n in s.llm_chain.split(",") if n.strip()) if m]
        if not members:
            return MockLLM()
        return members[0] if len(members) == 1 else FallbackLLM(members)
    use_anthropic = s.llm_provider == "anthropic" or (s.llm_provider == "auto" and bool(s.anthropic_api_key))
    return AnthropicLLM() if use_anthropic else MockLLM()


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = build_llm()
    return _llm


def set_llm(llm: LLM | None) -> None:
    """Test hook."""
    global _llm
    _llm = llm
