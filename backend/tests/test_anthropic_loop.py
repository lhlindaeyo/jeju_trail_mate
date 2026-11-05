"""Exercises AnthropicLLM's tool loop against a stub client (no network, no key)."""
from types import SimpleNamespace as NS

from app.agent.llm import AnthropicLLM, ToolSpec


class _StubMessages:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def _llm(responses):
    llm = AnthropicLLM.__new__(AnthropicLLM)
    import anthropic

    llm._anthropic, llm.model, llm.effort = anthropic, "claude-opus-5-5", "medium"
    stub = _StubMessages(responses)
    llm.client = NS(beta=NS(messages=stub))
    return llm, stub


def test_tool_loop_round_trip():
    tool_turn = NS(stop_reason="tool_use", content=[NS(type="text", text="확인할게요."),
                                                     NS(type="tool_use", id="tu_1", name="get_route", input={"route": "7"})])
    final = NS(stop_reason="end_turn", content=[NS(type="text", text="7코스는 17.6km입니다.")])
    llm, stub = _llm([tool_turn, final])
    seen = []
    run = llm.run_tools("sys", "질문", [ToolSpec("get_route", "d", {"type": "object"})],
                        lambda n, a: seen.append((n, a)) or {"distance_km": 17.6})
    assert run.text == "7코스는 17.6km입니다." and seen == [("get_route", {"route": "7"})]
    first = stub.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"] and first["output_config"] == {"effort": "medium"}
    second_msgs = stub.calls[1]["messages"]
    assert second_msgs[1]["role"] == "assistant" and second_msgs[2]["content"][0]["tool_use_id"] == "tu_1"


def test_tool_error_is_returned_not_raised_and_refusal_handled():
    tool_turn = NS(stop_reason="tool_use", content=[NS(type="tool_use", id="tu_1", name="get_route", input={})])
    refusal = NS(stop_reason="refusal", content=[])
    llm, stub = _llm([tool_turn, refusal])

    def boom(n, a):
        raise ValueError("missing route")

    run = llm.run_tools("sys", "q", [ToolSpec("get_route", "d", {"type": "object"})], boom)
    assert run.refused and run.tool_calls[0].ok is False
    assert stub.calls[1]["messages"][2]["content"][0]["is_error"] is True
