"""AgentPort: asks an agent (normally an LLM) for its next action."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolOutcome:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class AgentStep:
    """One model response."""

    tool_calls: list[ToolCall]
    text: str
    reasoning: list[str]  # reasoning summaries, where the model API exposes them
    stop_reason: str
    usage: dict = field(default_factory=dict)
    raw: dict | None = None  # provider response, for the harness record


class AgentPort(ABC):
    model: str

    @abstractmethod
    def start(self, system_prompt: str, tools: list[ToolSpec]) -> None:
        """Begin a fresh conversation. Nothing carries over from earlier games."""

    @abstractmethod
    def step(self, *, prompt: str | None = None, tool_outcomes: list[ToolOutcome] | None = None) -> AgentStep:
        """Deliver tool results and/or a new prompt, and return the agent's next response.

        Every tool call from the previous step must be answered in `tool_outcomes`
        before (or together with) a new prompt.
        """
