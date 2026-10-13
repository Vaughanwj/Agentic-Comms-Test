"""LlmAgent against a fake client: request shape and conversation handling."""
import copy

from anthropic.types import Message

from adapters.llm_agent import LlmAgent
from ports.agent import ToolOutcome, ToolSpec


def response(content, stop_reason="tool_use", **extra):
    return Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7,
                  "cache_creation_input_tokens": 3},
        **extra,
    })


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def stream(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return FakeStream(self.responses.pop(0))


TOOLS = [ToolSpec("read_messages", "Read.", {"type": "object", "properties": {}, "required": [],
                                              "additionalProperties": False})]


def test_request_shape_and_step_parsing():
    client = FakeClient([response([
        {"type": "thinking", "thinking": "Check messages first.", "signature": "sig"},
        {"type": "text", "text": "Reading."},
        {"type": "tool_use", "id": "tu_1", "name": "read_messages", "input": {}},
    ])])
    agent = LlmAgent("claude-opus-5-5", effort="medium", max_tokens=32000, client=client)
    agent.start("SYSTEM", TOOLS)
    step = agent.step(prompt="It is your turn.")

    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"] == {"effort": "medium"}
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["system"] == "SYSTEM"
    assert req["tools"][0]["strict"] is True
    assert req["messages"] == [{"role": "user", "content": [{"type": "text", "text": "It is your turn."}]}]

    assert [c.name for c in step.tool_calls] == ["read_messages"]
    assert step.reasoning == ["Check messages first."]
    assert step.text == "Reading."
    assert step.usage["cache_read_input_tokens"] == 7
    assert step.raw["served_model"] == "claude-opus-5-5"
    assert "signature" not in step.raw["content"][0]


def test_history_is_append_only_and_tool_results_come_first():
    client = FakeClient([
        response([{"type": "tool_use", "id": "tu_1", "name": "read_messages", "input": {}}]),
        response([{"type": "text", "text": "Done."}], stop_reason="end_turn"),
        response([{"type": "text", "text": "Ok."}], stop_reason="end_turn"),
    ])
    agent = LlmAgent("claude-opus-5-5", client=client)
    agent.start("S", TOOLS)
    agent.step(prompt="turn 1")
    agent.step(tool_outcomes=[ToolOutcome("tu_1", "No new messages.")])
    agent.step(prompt="turn 2")

    first, second, third = (r["messages"] for r in client.requests)
    assert second[: len(first)] == first and third[: len(second)] == second
    assert second[-1]["content"] == [{"type": "tool_result", "tool_use_id": "tu_1", "content": "No new messages."}]
    assert third[-1] == {"role": "user", "content": [{"type": "text", "text": "turn 2"}]}


def test_cut_off_calls_are_not_run_and_get_answered_later():
    client = FakeClient([
        response([{"type": "tool_use", "id": "tu_9", "name": "read_messages", "input": {}}], stop_reason="max_tokens"),
        response([{"type": "text", "text": "Ok."}], stop_reason="end_turn"),
    ])
    agent = LlmAgent("claude-opus-5-5", client=client)
    agent.start("S", TOOLS)
    step = agent.step(prompt="turn")
    assert step.tool_calls == [] and step.stop_reason == "max_tokens"
    agent.step(prompt="next turn")
    content = client.requests[1]["messages"][-1]["content"]
    assert content[0]["tool_use_id"] == "tu_9" and content[0]["is_error"] is True
    assert content[1] == {"type": "text", "text": "next turn"}


def test_refusal_is_reported_not_retried_elsewhere():
    client = FakeClient([response([], stop_reason="refusal",
                                  stop_details={"type": "refusal", "category": "cyber", "explanation": "x"})])
    agent = LlmAgent("claude-opus-5-5", client=client)
    agent.start("S", TOOLS)
    step = agent.step(prompt="turn")
    assert step.stop_reason == "refusal" and step.tool_calls == []
    assert step.raw["stop_details"]["category"] == "cyber"
    assert "fallbacks" not in client.requests[0]
