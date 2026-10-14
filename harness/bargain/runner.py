"""Run one negotiation session end to end and write its JSONL record.

A session is one or more pots negotiated in turn by the same agents, in one
continuous conversation each, so they remember earlier pots.
"""
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
from core.bargaining import Bargain, rotated
from harness.activation import Activations
from harness.bargain.config import BargainConfig
from harness.bargain.prompts import (build_system_prompt, counter_prompt, draft_prompt, pot_start_notice,
                                     propose_prompt, result_notice, talk_prompt, vote_prompt)
from harness.bargain.tools import BargainTools, tool_specs
from harness.game_runner import FATAL_ERRORS
from ports.agent import AgentPort

HARNESS_VERSION = "bargain-2"

# (player, first pot's fallback, proposer order) -> agent
NegotiatorFactory = Callable[[str, int, tuple[str, ...]], AgentPort]


@dataclass
class BargainResult:
    cell: str
    game_index: int
    end_reason: str  # "deal" / "no_deal" for a single pot, "complete" for a session, or "error"
    deal_round: int | None  # single pot only
    payoffs: dict | None  # points per agent, totalled over the session
    error_type: str | None = None
    error_message: str | None = None
    pots_with_deal: int | None = None
    winner: None = None  # unused; keeps the shape run_games prints

    @property
    def fatal(self) -> bool:
        return self.error_type in FATAL_ERRORS

    def describe(self) -> str:
        totals = ", ".join(f"{p} {v}" for p, v in (self.payoffs or {}).items())
        if self.end_reason == "deal":
            return f"deal in round {self.deal_round}: {totals}"
        if self.end_reason == "complete":
            return f"deals in {self.pots_with_deal} pots; totals {totals}"
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
        return ScriptedNegotiator(player, config.names, fallback, misbehaviour.get(player, NegotiatorMisbehaviour()))
    return make


def run_bargain_game(config: BargainConfig, game_index: int, records_path: Path,
                     factory: NegotiatorFactory) -> BargainResult:
    recorder = JsonlRecorder(Path(records_path))
    try:
        return _Session(config, game_index, recorder, factory).run()
    finally:
        recorder.close()


class _Session:
    def __init__(self, config: BargainConfig, game_index: int, recorder: JsonlRecorder, factory: NegotiatorFactory):
        self.config = config
        self.game_index = game_index
        self.recorder = recorder
        self.fallbacks, self.order, self.rng = config.draw(game_index)
        names = config.names
        self.game = self._game_for(1)
        self.transport = TappedTransport(SocketHubTransport(names, config.socket_host, config.socket_port), recorder)
        self.tools = {p: BargainTools(p, self.game, self.transport, recorder, config.messaging) for p in names}
        self.agents = {p: factory(p, self.fallbacks[0][p], self.order) for p in names}
        self.activations = Activations(recorder, config.max_steps_per_activation)
        self.notices: dict[str, list[str]] = {p: [] for p in names}
        self.totals = {p: 0 for p in names}
        self.deals = 0
        self.turn = 0
        self.error: Exception | None = None

    @property
    def session(self) -> bool:
        return self.config.pots > 1

    def _game_for(self, pot: int) -> Bargain:
        c = self.config
        return Bargain(c.names, c.pot, c.shrink, c.max_rounds, self.fallbacks[pot - 1], rotated(self.order, pot - 1))

    def run(self) -> BargainResult:
        config, names = self.config, self.config.names
        try:
            self.transport.open()
            prompts = {p: build_system_prompt(config, p, self.fallbacks[0][p], self.order) for p in names}
            specs = {p: tool_specs(p, names, config.messaging, config.draft) for p in names}
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
                fallbacks=self.fallbacks[0],
                fallbacks_by_pot=self.fallbacks,
                proposer_order=self.order,
                system_prompts=prompts,
                tools={p: [dataclasses.asdict(s) for s in specs[p]] for p in names},
            )
            for p in names:
                self.agents[p].start(prompts[p], specs[p])
            for pot in range(1, config.pots + 1):
                self._play_pot(pot)
        except Exception as exc:  # recorded, then reported to the caller
            self.error = exc
            self.recorder.record("error", error_type=type(exc).__name__, message=str(exc),
                                 traceback=traceback.format_exc())
        finally:
            self._finish()
        return self._result()

    def _play_pot(self, pot: int) -> None:
        config, names = self.config, self.config.names
        game = self.game = self.game if pot == 1 else self._game_for(pot)
        for p in names:
            self.tools[p].game, self.tools[p].pot = game, pot
        where = pot if self.session else None
        self.recorder.record("pot_start", pot=pot, size=game.pot, fallbacks=game.fallbacks,
                             proposer_order=game.proposer_order)
        if self.session:
            for p in names:
                self.notices[p].append(pot_start_notice(pot, config.pots, game.pot, game.fallbacks[p],
                                                        game.proposer_order, self.totals))

        while not game.over:
            for turn in range(1, config.talk_turns + 1):
                for p in self._shuffled():
                    self._activation(p, talk_prompt(where, game.round, turn, config.talk_turns, game.pot,
                                                    game.proposer), "talk")
            if config.draft:
                game.open_draft()
                self._activation(game.proposer, draft_prompt(where, game.round, game.pot), "draft")
                game.open_counter()
                for turn in range(1, config.counter_turns + 1):
                    for p in self._shuffled():
                        self._activation(p, counter_prompt(where, game.round, turn, config.counter_turns, game.draft),
                                         "counter")
            draft = game.draft
            game.open_proposals()
            self._activation(game.proposer, propose_prompt(where, game.round, game.pot, draft), "propose")
            if game.proposal is not None:
                game.open_voting()
                for p in self._shuffled():
                    self._activation(p, vote_prompt(where, game.proposal), "vote")
            result = game.close_round()
            self.recorder.record("round_result", pot=pot, draft=draft, result=result)
            notice = result_notice(where, result, None if game.over else game.pot, config.reveal_votes)
            for p in names:
                self.notices[p].append(notice)

        for p in names:
            self.totals[p] += game.payoffs[p]
        self.deals += game.deal is not None
        self.recorder.record("pot_end", pot=pot, deal_round=game.deal.round if game.deal else None,
                             payoffs=game.payoffs, totals=dict(self.totals))

    def _shuffled(self) -> list[str]:
        return self.rng.sample(self.config.names, len(self.config.names))

    def _activation(self, player: str, prompt: str, phase: str) -> None:
        self.turn += 1
        notices, self.notices[player] = self.notices[player], []
        full_prompt = "\n".join(notices + [prompt])
        self.activations.run(player, self.agents[player], self.tools[player].dispatch, full_prompt, self.turn, phase)

    def _end_reason(self) -> str:
        if self.error:
            return "error"
        if self.session:
            return "complete"
        return "deal" if self.game.deal else "no_deal"

    def _finish(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass
        single = self.game.deal if not self.session else None
        self.recorder.record("game_end", end_reason=self._end_reason(), deal_round=single.round if single else None,
                             pots_with_deal=self.deals, payoffs=dict(self.totals), fallbacks=self.fallbacks[0])

    def _result(self) -> BargainResult:
        single = self.game.deal if not self.session else None
        return BargainResult(
            cell=self.config.cell,
            game_index=self.game_index,
            end_reason=self._end_reason(),
            deal_round=single.round if single else None,
            payoffs=dict(self.totals),
            error_type=type(self.error).__name__ if self.error else None,
            error_message=str(self.error) if self.error else None,
            pots_with_deal=self.deals,
        )
