"""One agent turn ("activation"), shared by every game.

The agent gets a prompt, calls tools until it replies without one, and the
harness runs each call. A turn that hits the step cap ends there; the results
of its last calls reach the agent with its next prompt.
"""
from __future__ import annotations

from typing import Callable

from ports.agent import AgentPort, AgentStep, ToolCall, ToolOutcome
from ports.recorder import RecorderPort


class Activations:
    def __init__(self, recorder: RecorderPort, max_steps: int):
        self.recorder = recorder
        self.max_steps = max_steps
        self._pending: dict[str, list[ToolOutcome]] = {}

    def run(self, player: str, agent: AgentPort, dispatch: Callable[[ToolCall, int], ToolOutcome],
            prompt: str, activation: int, phase: str) -> str:
        """Run one turn and return what ended it (a stop reason, or "step_cap")."""
        self.recorder.record("activation_start", player=player, activation=activation, phase=phase, prompt=prompt)
        carried = self._pending.pop(player, [])
        step = agent.step(prompt=prompt, tool_outcomes=carried or None)
        steps = 1
        self._record_step(player, activation, steps, step)
        ended_by = step.stop_reason
        while step.tool_calls:
            outcomes = [dispatch(call, activation) for call in step.tool_calls]
            if steps >= self.max_steps:
                self._pending[player] = outcomes
                ended_by = "step_cap"
                break
            step = agent.step(tool_outcomes=outcomes)
            steps += 1
            self._record_step(player, activation, steps, step)
            ended_by = step.stop_reason
        self.recorder.record("activation_end", player=player, activation=activation, phase=phase,
                             steps=steps, ended_by=ended_by)
        return ended_by

    def _record_step(self, player: str, activation: int, index: int, step: AgentStep) -> None:
        self.recorder.record(
            "agent_step",
            player=player,
            activation=activation,
            step=index,
            stop_reason=step.stop_reason,
            text=step.text,
            reasoning=step.reasoning,
            tool_calls=[{"call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in step.tool_calls],
            usage=step.usage,
            raw=step.raw,
        )
