"""Divide-the-pot bargaining (legislative bargaining).

N players split a pot of points. Each round one player proposes a split and
everyone votes yes or no; a strict majority passes it and ends the game. A
failed round shrinks the pot and the next player in the order proposes. If no
proposal passes by the last round, every player gets its private fallback.

Optionally, the proposer first posts a non-binding draft, the others respond in
a counter phase, and only then does the proposer make the final proposal.

Pure logic: no I/O, no agents, no clock.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class BargainError(ValueError):
    """An illegal move; the message is shown to the agent."""


class Phase(str, Enum):
    TALK = "talk"
    DRAFT = "draft"
    COUNTER = "counter"
    PROPOSE = "propose"
    VOTE = "vote"
    DONE = "done"


@dataclass(frozen=True)
class Proposal:
    round: int
    proposer: str
    shares: dict[str, int]


@dataclass(frozen=True)
class RoundResult:
    round: int
    pot: int
    proposal: Proposal | None
    ballots: dict[str, str]  # player -> "yes" / "no"; a missing vote counts as no
    missing: tuple[str, ...]
    yes: int
    no: int
    passed: bool


def pot_for_round(start: int, shrink: float, round_: int) -> int:
    """Whole points available in a round: the start pot shrunk once per failed round."""
    return math.floor(start * (1 - shrink) ** (round_ - 1) + 1e-9)


class Bargain:
    def __init__(self, players: tuple[str, ...], pot: int, shrink: float, max_rounds: int,
                 fallbacks: dict[str, int], proposer_order: tuple[str, ...]):
        if len(players) < 3:
            raise ValueError("bargaining needs at least three players")
        if set(fallbacks) != set(players) or sorted(proposer_order) != sorted(players):
            raise ValueError("fallbacks and proposer order must cover every player exactly once")
        if not 0 <= shrink < 1 or pot < 1 or max_rounds < 1:
            raise ValueError("bad pot, shrink or round limit")
        self.players = tuple(players)
        self.start_pot = pot
        self.shrink = shrink
        self.max_rounds = max_rounds
        self.fallbacks = dict(fallbacks)
        self.proposer_order = tuple(proposer_order)

        self.round = 1
        self.phase = Phase.TALK
        self.proposal: Proposal | None = None
        self.draft: Proposal | None = None
        self.ballots: dict[str, str] = {}
        self.results: list[RoundResult] = []
        self.payoffs: dict[str, int] | None = None

    @property
    def majority(self) -> int:
        return len(self.players) // 2 + 1

    @property
    def pot(self) -> int:
        return pot_for_round(self.start_pot, self.shrink, self.round)

    @property
    def proposer(self) -> str:
        return self.proposer_order[(self.round - 1) % len(self.players)]

    @property
    def over(self) -> bool:
        return self.phase is Phase.DONE

    @property
    def deal(self) -> RoundResult | None:
        return next((r for r in self.results if r.passed), None)

    # phases --------------------------------------------------------------

    def open_draft(self) -> None:
        self._expect(Phase.TALK)
        self.phase = Phase.DRAFT

    def open_counter(self) -> None:
        self._expect(Phase.DRAFT)
        self.phase = Phase.COUNTER

    def open_proposals(self) -> None:
        if self.phase not in (Phase.TALK, Phase.COUNTER):
            raise RuntimeError(f"cannot open proposals from {self.phase.value}")
        self.phase = Phase.PROPOSE

    def open_voting(self) -> None:
        self._expect(Phase.PROPOSE)
        self.phase = Phase.VOTE

    def close_round(self) -> RoundResult:
        if self.phase not in (Phase.PROPOSE, Phase.VOTE):
            raise RuntimeError("no round to close")
        ballots = {p: self.ballots.get(p, "no") for p in self.players} if self.proposal else {}
        missing = tuple(p for p in self.players if p not in self.ballots) if self.proposal else ()
        yes = sum(v == "yes" for v in ballots.values())
        passed = self.proposal is not None and yes >= self.majority
        result = RoundResult(self.round, self.pot, self.proposal, ballots, missing, yes, len(ballots) - yes, passed)
        self.results.append(result)

        if passed:
            self.payoffs = dict(self.proposal.shares)
            self.phase = Phase.DONE
        elif self.round >= self.max_rounds:
            self.payoffs = dict(self.fallbacks)
            self.phase = Phase.DONE
        else:
            self.round += 1
            self.phase = Phase.TALK
            self.proposal = None
            self.draft = None
            self.ballots = {}
        return result

    # moves ---------------------------------------------------------------

    def post_draft(self, player: str, shares: dict) -> Proposal:
        self._expect(Phase.DRAFT, "Drafts are not open right now.")
        if player != self.proposer:
            raise BargainError(f"It is {self.proposer}'s turn to post a draft, not yours.")
        if self.draft is not None:
            raise BargainError("You have already posted this round's draft.")
        self.draft = Proposal(self.round, player, self._check_split(shares))
        return self.draft

    def propose(self, player: str, shares: dict) -> Proposal:
        self._expect(Phase.PROPOSE, "Proposals are not open right now.")
        if player != self.proposer:
            raise BargainError(f"It is {self.proposer}'s turn to propose, not yours.")
        if self.proposal is not None:
            raise BargainError("You have already made this round's proposal.")
        self.proposal = Proposal(self.round, player, self._check_split(shares))
        return self.proposal

    def _check_split(self, shares: dict) -> dict[str, int]:
        if set(shares) != set(self.players):
            raise BargainError(f"Give a share for every player: {', '.join(self.players)}.")
        clean = {}
        for p in self.players:
            value = shares[p]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BargainError(f"{p}'s share must be a whole number of points, zero or more.")
            clean[p] = value
        if sum(clean.values()) != self.pot:
            raise BargainError(f"The shares add up to {sum(clean.values())}; they must add up to the pot, {self.pot}.")
        return clean

    def vote(self, player: str, choice: str) -> None:
        self._expect(Phase.VOTE, "Voting is not open right now.")
        if player not in self.players:
            raise BargainError(f"Unknown player: {player}")
        if choice not in ("yes", "no"):
            raise BargainError("Vote yes or no.")
        if player in self.ballots:
            raise BargainError("You have already voted this round.")
        self.ballots[player] = choice

    def _expect(self, phase: Phase, message: str | None = None) -> None:
        if self.phase is not phase:
            if message:
                raise BargainError(message)
            raise RuntimeError(f"expected phase {phase.value}, in {self.phase.value}")


def rotated(order: tuple[str, ...], steps: int) -> tuple[str, ...]:
    """The proposer order for a later pot: the same cycle, starting `steps` places along."""
    steps %= len(order)
    return tuple(order[steps:] + order[:steps])
