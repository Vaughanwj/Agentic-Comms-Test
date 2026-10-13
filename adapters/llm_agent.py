"""LlmAgent: an AgentPort backed by the Claude Messages API.

The conversation is append-only, so the prompt cache stays warm across a
game and replayed thinking blocks stay valid.

No refusal fallback is configured on purpose: a fallback would hand the turn
to a different model, and Phase 1 requires the same model on both sides. A
refusal is returned as a step with stop_reason "refusal" and recorded.
"""
from __future__ import annotations

from typing import Any

from ports.agent import AgentPort, AgentStep, ToolCall, ToolOutcome, ToolSpec

_NOT_RUN = "This tool call was not run because the response was cut off."


class LlmAgent(AgentPort):
    def __init__(
        self,
        model: str,
        effort: str = "medium",
        max_tokens: int = 32000,
        proxy: str | None = None,
        workspace_id: str | None = None,
        max_retries: int = 4,
        client: Any = None,
    ):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client
        self._proxy = proxy
        self._workspace_id = workspace_id
        self._max_retries = max_retries
        self._system = ""
        self._tools: list[dict] = []
        self._messages: list[dict] = []
        self._unanswered: list[str] = []

    @property
    def client(self):
        if self._client is None:
            import anthropic

            http_client = anthropic.DefaultHttpxClient(proxy=self._proxy) if self._proxy else None
            # Keys not scoped to one workspace must name the workspace on every request.
            headers = {"anthropic-workspace-id": self._workspace_id} if self._workspace_id else None
            self._client = anthropic.Anthropic(max_retries=self._max_retries, http_client=http_client,
                                               default_headers=headers)
        return self._client

    def start(self, system_prompt: str, tools: list[ToolSpec]) -> None:
        self._system = system_prompt
        # Strict schemas guarantee arguments match the schema. Tool inputs here are a
        # few dozen bytes, so eager input streaming would buy nothing.
        self._tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
            for t in tools
        ]
        self._messages = []
        self._unanswered = []

    def step(self, *, prompt: str | None = None, tool_outcomes: list[ToolOutcome] | None = None) -> AgentStep:
        content: list[dict] = []
        answered = {o.call_id for o in tool_outcomes or []}
        for call_id in self._unanswered:
            if call_id not in answered:
                content.append({"type": "tool_result", "tool_use_id": call_id, "content": _NOT_RUN, "is_error": True})
        for outcome in tool_outcomes or []:
            block = {"type": "tool_result", "tool_use_id": outcome.call_id, "content": outcome.content}
            if outcome.is_error:
                block["is_error"] = True
            content.append(block)
        if prompt:
            content.append({"type": "text", "text": prompt})
        if not content:
            raise ValueError("step() needs a prompt or tool outcomes")
        self._messages.append({"role": "user", "content": content})

        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system,
            tools=self._tools,
            messages=self._messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
        ) as stream:
            response = stream.get_final_message()

        if response.content:
            self._messages.append({"role": "assistant", "content": response.content})

        calls, texts, reasoning = [], [], []
        for block in response.content:
            if block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
            elif block.type == "text":
                texts.append(block.text)
            elif block.type == "thinking" and block.thinking:
                reasoning.append(block.thinking)

        stop_reason = response.stop_reason or ""
        if stop_reason in ("max_tokens", "refusal"):
            # Never run calls from a cut-off or declined response; answer them as not run.
            self._unanswered = [c.call_id for c in calls]
            calls = []
        else:
            self._unanswered = [c.call_id for c in calls]

        raw = response.to_dict()
        for block in raw.get("content", []):
            block.pop("signature", None)
        if getattr(response, "stop_details", None) is not None:
            raw["stop_details"] = response.stop_details.to_dict()
        return AgentStep(
            tool_calls=calls,
            text="\n".join(texts),
            reasoning=reasoning,
            stop_reason=stop_reason,
            usage=response.usage.to_dict(),
            raw={"id": response.id, "served_model": response.model, **raw},
        )
