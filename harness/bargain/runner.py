"""Run one negotiation end to end and write its JSONL record."""
from __future__ import annotations

import dataclasses
import platform
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from adapters.jsonl_recorder import JsonlRecorder
from adapters.llm_agent import LlmAgent
from adapters.scripted_negotiator import NegotiatorMisbehaviour, ScriptedNegotiator
from adapters.socket_hub_transport import SocketHubTransport
from adapters.tapped_transport import TappedTransport
from core.bargaining import Bargain
from harness.activation import Activations
from harness.bargain.config import BargainConfig
from harness.bargain.prompts import build_system_prompt, propose_prompt, result_notice, talk_prompt, vote_prompt
from harness.bargain.tools import BargainTools, tool_specs
from harness.game_runner import FATAL_ERRORS
from ports.agent import AgentPort

HARNESS_VERSION = "bargain-1"

# (player, fallback, proposer order) -> agent
NegotiatorFactory = Callable[[str, int, tuple[str, ...]], AgentPort]


@dataclass
class BargainResult:
    cell: str
    game_index: int
    end_reason: str  # "deal", "no_deal" or "error"
    deal_round: int | None
    payoffs: dict | None
    error_type: str | None = None
    error_message: str | None = None
    winner: None = None  # unused; keeps the shape run_games prints

    @property
    def fatal(self) -> bool:
        return self.error_type in FATAL_ERRORS

    def describe(self) -> str:
        if self.end_reason == "deal":
            return f"deal in round {self.deal_round}: " + ", ".join(f"{p} {v}" for p, v in self.payoffs.items())
        return self.end_reason


def llm_negotiators(config: BargainConfig, proxy: str | None, workspace_id: str | None) -> NegotiatorFactory:
    def make(player: str, fallback: int, order: tuple[str, ...]) -> AgentPort:
        return LlmAgent(config.model, effort=config.effort, max_tokens=config.max_tokens, proxy=proxy,
                        workspace_id=workspace_id)
    return make


def scripted_negotiators(config: BargainConfig,
                         misbehaviour: dict[str, NegotiatorMisbehaviour] | None = None) -> NegotiatorFactory:
    misbehaviour = misbehaviour or {}

    def make(player: str, fallback: int, order: tuple[str, ...]) -> AgentPort:
        return ScriptedNegotiator(player, config.names, fallback, order,
                                  misbehaviour.get(player, NegotiatorMisbehaviour()))
    return make


def run_bargain_game(config: BargainConfig, game_index: int, records_path: Path,
                     factory: NegotiatorFactory) -> BargainResult:
    recorder = JsonlRecorder(Path(records_path))
    try:
        return _Negotiation(config, game_index, recorder, factory).run()
    finally:
        recorder.close()


class _Negotiation:
    def __init__(self, config: BargainConfig, game_index: int, recorder: JsonlRecorder, factory: NegotiatorFactory):
        self.config = config
        self.game_index = game_index
        self.recorder = recorder
        self.fallbacks, self.order, self.rng = config.draw(game_index)
        names = config.names
        self.game = Bargain(names, config.pot, config.shrink, config.max_rounds, self.fallbacks, self.order)
        self.transport = TappedTransport(SocketHubTransport(names, config.socket_host, config.socket_port), recorder)
        self.tools = {p: BargainTools(p, self.game, self.transport, recorder, config.messaging) for p in names}
        self.agents = {p: factory(p, self.fallbacks[p], self.order) for p in names}
        self.activations = Activations(recorder, config.max_steps_per_activation)
        self.notices: dict[str, list[str]] = {p: [] for p in names}
        self.turn = 0
        self.error: Exception | None = None

    def run(self) -> BargainResult:
        config, game, names = self.config, self.game, self.config.names
        try:
            self.transport.open()
            prompts = {p: build_system_prompt(config, p, self.fallbacks[p], self.order) for p in names}
            specs = {p: tool_specs(p, names, config.messaging) for p in names}
            self.recorder.record(
                "game_start",
                game="bargain",
                harness_version=HARNESS_VERSION,
                python=platform.python_version(),
                cell=config.cell,
                game_index=self.game_index,
                models={p: self.agents[p].model for p in names},
                effort=config.effort,
                config=config.snapshot(),
                fallbacks=self.fallbacks,
                proposer_order=self.order,
                system_prompts=prompts,
                tools={p: [dataclasses.asdict(s) for s in specs[p]] for p in names},
            )
            for p in names:
                self.agents[p].start(prompts[p], specs[p])

            while not game.over:
                for turn in range(1, config.talk_turns + 1):
                    for p in self.rng.sample(names, len(names)):
                        self._activation(p, talk_prompt(game.round, turn, config.talk_turns, game.pot, game.proposer),
                                         "talk")
                game.open_proposals()
                self._activation(game.proposer, propose_prompt(game.round, game.pot), "propose")
                if game.proposal is not None:
                    game.open_voting()
                    for p in self.rng.sample(names, len(names)):
                        self._activation(p, vote_prompt(game.proposal), "vote")
                result = game.close_round()
                self.recorder.record("round_result", result=result)
                notice = result_notice(result, None if game.over else game.pot, config.reveal_votes)
                for p in names:
                    self.notices[p].append(notice)
        except Exception as exc:  # recorded, then reported to the caller
            self.error = exc
            self.recorder.record("error", error_type=type(exc).__name__, message=str(exc),
                                 traceback=traceback.format_exc())
        finally:
            self._finish()
        deal = game.deal
        return BargainResult(
            cell=config.cell,
            game_index=self.game_index,
            end_reason="error" if self.error else ("deal" if deal else "no_deal"),
            deal_round=deal.round if deal else None,
            payoffs=game.payoffs,
            error_type=type(self.error).__name__ if self.error else None,
            error_message=str(self.error) if self.error else None,
        )

    def _activation(self, player: str, prompt: str, phase: str) -> None:
        self.turn += 1
        notices, self.notices[player] = self.notices[player], []
        full_prompt = "\n".join(notices + [prompt])
        self.activations.run(player, self.agents[player], self.tools[player].dispatch, full_prompt, self.turn, phase)

    def _finish(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass
        deal = self.game.deal
        self.recorder.record(
            "game_end",
            end_reason="error" if self.error else ("deal" if deal else "no_deal"),
            deal_round=deal.round if deal else None,
            rounds_played=len(self.game.results),
            payoffs=self.game.payoffs,
            fallbacks=self.fallbacks,
        )
