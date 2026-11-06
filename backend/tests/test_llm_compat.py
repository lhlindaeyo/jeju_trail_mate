"""OpenAI-compatible provider (Ollama/Gemini/Groq/...) and the fallback chain — stub HTTP, no network."""
import json

import httpx
import pytest

from app.agent import llm as llm_mod
from app.agent.llm import FallbackLLM, LLMUnavailable, MockLLM, OpenAICompatLLM, ToolSpec, is_local_url
from app.config import Settings

TOOLS = [ToolSpec("get_weather", "날씨", {"type": "object", "properties": {}}),
         ToolSpec("get_route", "코스", {"type": "object", "properties": {"route": {"type": "string"}}})]


def _msg(content=None, tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tc(name, args, id="c1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _llm(handler, api_key=None, **kw):
    return OpenAICompatLLM("t", "http://localhost:11434/v1", "qwen3:4b", api_key,
                           client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_tool_loop_round_trip():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_msg(tool_calls=[_tc("get_route", {"route": "7"})]) if len(bodies) == 1
                              else _msg("7코스는 17.6km입니다."))

    seen = []
    run = _llm(handler).run_tools("sys", "질문", TOOLS, lambda n, a: seen.append((n, a)) or {"km": 17.6})
    assert run.text == "7코스는 17.6km입니다." and seen == [("get_route", {"route": "7"})] and run.provider == "t"
    first, second = bodies
    assert first["tools"][0]["type"] == "function" and first["messages"][0] == {"role": "system", "content": "sys"}
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"km": 17.6}'}


def test_bad_tool_arguments_and_tool_errors_go_back_to_the_model():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(200, json=_msg(tool_calls=[{"id": "a", "type": "function", "function": {"name": "get_route", "arguments": "{oops"}}]))
        if n["i"] == 2:
            return httpx.Response(200, json=_msg(tool_calls=[_tc("get_route", {}, "b")]))
        return httpx.Response(200, json=_msg("확인하지 못했어요."))

    def boom(name, args):
        raise ValueError("missing route")

    run = _llm(handler).run_tools("sys", "q", TOOLS, boom)
    assert [c.ok for c in run.tool_calls] == [False, False] and run.text == "확인하지 못했어요."


def test_model_that_skips_tools_is_grounded_by_keyword_intent():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_msg("내일은 아마 맑을 거예요."))  # answered from memory

    seen = []
    run = _llm(handler).run_tools("sys", "...\n[사용자 질문]\n오늘 날씨 어때?", TOOLS,
                                  lambda n, a: seen.append(n) or {"status": "ok"})
    assert seen == ["get_weather"] and run.tool_calls[0].ok
    # the model is asked again, now with the verified tool result in context
    assert bodies[1]["messages"][-1]["role"] == "tool" and bodies[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "get_weather"


def test_small_talk_is_not_forced_into_a_tool():
    seen = []
    run = _llm(lambda req: httpx.Response(200, json=_msg("안녕하세요!"))).run_tools("s", "[사용자 질문]\n안녕", TOOLS, lambda n, a: seen.append(n))
    assert run.text == "안녕하세요!" and seen == []


def test_reasoning_block_is_stripped_and_empty_reply_is_unavailable():
    ok = _llm(lambda req: httpx.Response(200, json=_msg("<think>음…</think>\n정답입니다.")))
    assert ok.generate("s", "u") == "정답입니다."
    with pytest.raises(LLMUnavailable):
        _llm(lambda req: httpx.Response(200, json=_msg("<think>끝나지 않은 생각"))).generate("s", "u")


def test_keys_rotate_on_rate_limit_and_round_robin_on_success():
    used = []

    def handler(req):
        key = req.headers["authorization"].removeprefix("Bearer ")
        used.append(key)
        return httpx.Response(429 if key == "k1" else 200, json=_msg("ok"))

    llm = _llm(handler, api_key="k1, k2")
    assert llm.generate("s", "u") == "ok"
    assert used == ["k1", "k2"]  # k1 rejected -> k2 used
    llm.generate("s", "u")
    assert used == ["k1", "k2", "k2"]  # k1 rests after a rejection, so it is not retried on every call

    both = _llm(lambda req: httpx.Response(200, json=_msg("ok")), api_key="a,b")
    seen = []
    both.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: seen.append(req.headers["authorization"]) or httpx.Response(200, json=_msg("ok"))))
    both.generate("s", "u"), both.generate("s", "u"), both.generate("s", "u")
    assert seen == ["Bearer a", "Bearer b", "Bearer a"]  # healthy keys share the load


def test_all_keys_rejected_or_server_down_is_unavailable():
    with pytest.raises(LLMUnavailable):
        _llm(lambda req: httpx.Response(429, json={}), api_key="a,b").generate("s", "u")
    with pytest.raises(LLMUnavailable):
        _llm(lambda req: httpx.Response(500, text="x")).generate("s", "u")

    def refuse(req):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMUnavailable):
        _llm(refuse).generate("s", "u")


class _Down:
    name, external = "down", False

    def generate(self, *a, **k):
        raise LLMUnavailable("down")

    run_tools = generate


def test_fallback_chain_moves_on_and_reports_external_conservatively():
    chain = FallbackLLM([_Down(), MockLLM()])
    run = chain.run_tools("s", "[사용자 질문]\n날씨", TOOLS, lambda n, a: {"status": "unavailable", "message": "예보 확인 필요"})
    assert run.provider == "mock" and "예보" in run.text
    with pytest.raises(LLMUnavailable):
        chain.generate("s", "u")  # mock has no free text either -> last error surfaces
    cloud = OpenAICompatLLM("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "m", "k")
    assert FallbackLLM([OpenAICompatLLM("ollama", "http://localhost:11434/v1", "m"), cloud]).external is True
    assert FallbackLLM([OpenAICompatLLM("ollama", "http://localhost:11434/v1", "m"), MockLLM()]).external is False


@pytest.mark.parametrize("url,local", [
    ("http://localhost:11434/v1", True), ("http://127.0.0.1:8000/v1", True), ("http://ollama:11434/v1", True),
    ("http://host.docker.internal:11434/v1", True), ("http://192.168.0.5:8000/v1", True),
    ("https://api.groq.com/openai/v1", False), ("https://generativelanguage.googleapis.com/v1beta/openai", False),
])
def test_is_local_url(url, local):
    assert is_local_url(url) is local


def test_chain_is_built_from_settings(monkeypatch):
    def build(**kw):
        monkeypatch.setattr(llm_mod, "get_settings", lambda: Settings(_env_file=None, **kw))
        return llm_mod.build_llm()

    chain = build(llm_chain="gemini, ollama ,nope,mock", gemini_api_key="k1,k2")
    assert isinstance(chain, FallbackLLM) and chain.name == "gemini>ollama>mock"  # unknown entry ignored
    assert build(llm_chain="gemini,groq").name == "mock"  # nothing configured -> offline mock, never a crash
    assert build(llm_chain="ollama").name == "ollama" and build(llm_chain="ollama").external is False
    assert build(llm_chain="", llm_provider="mock").name == "mock"  # legacy switch still works
    custom = build(llm_chain="openai", openai_base_url="http://localhost:8000/v1", openai_model="m")
    assert custom.name == "openai" and custom.model == "m"
