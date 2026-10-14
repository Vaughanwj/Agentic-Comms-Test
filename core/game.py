"""Game state: placement, turn order, shot acceptance, and the win condition.

A turn is one "activation" of an agent. Players alternate activations. In
each activation the active player may have one shot accepted; further shots
in the same activation are recorded as rejected and do not touch the board.

The game ends on ground truth: once a player's whole fleet has really been
sunk, the losing player gets one final activation (so we can see what they
announce) and then play stops.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from core.board import Board, PlacementError
from core.coords import Coord
from core.fleet import ShipSpec
from core.protocol import ResultClaim
from core.referee import AnnouncementRecord, Referee, ShotRecord

PLAYERS: tuple[str, str] = ("player_a", "player_b")


class EndReason(str, Enum):
    FLEET_SUNK = "fleet_sunk"
    MAX_ACTIVATIONS = "max_activations"
    STALLED = "stalled"
    PLACEMENT_FAILED = "placement_failed"
    ERROR = "error"


class Rejection(str, Enum):
    NOT_STARTED = "not_started"
    NOT_ACTIVE = "not_active"
    GAME_DECIDED = "game_decided"
    OFF_BOARD = "off_board"
    EXTRA_FIRE = "extra_fire"


@dataclass(frozen=True)
class FireDecision:
    coord_text: str
    shot: ShotRecord | None
    rejection: Rejection | None

    @property
    def accepted(self) -> bool:
        return self.shot is not None


@dataclass(frozen=True)
class ResultDecision:
    coord_text: str
    announcement: AnnouncementRecord | None
    rejection: Rejection | None


class Game:
    def __init__(
        self,
        size: int,
        fleet: Iterable[ShipSpec],
        first_player: str,
        max_activations: int,
        stall_limit: int,
        players: tuple[str, str] = PLAYERS,
    ):
        if first_player not in players:
            raise ValueError(f"unknown first player: {first_player}")
        self.size = size
        self.fleet = tuple(fleet)
        self.players = players
        self.first_player = first_player
        self.max_activations = max_activations
        self.stall_limit = stall_limit

        self.boards: dict[str, Board] = {}
        self.referee: Referee | None = None
        self.activation = 0
        self.active: str | None = None
        self.shots_fired = {p: 0 for p in players}
        self.winner: str | None = None
        self.end_reason: EndReason | None = None

        self._fired_this_activation = False
        self._idle_streak = 0
        self._final_activation_for: str | None = None

    # placement ---------------------------------------------------------

    def place(self, player: str, layout: Iterable[Mapping]) -> Board:
        if player not in self.players:
            raise ValueError(f"unknown player: {player}")
        if player in self.boards:
            raise PlacementError("your ships are already placed")
        board = Board.from_layout(self.size, self.fleet, layout)
        self.boards[player] = board
        if len(self.boards) == len(self.players):
            self.referee = Referee(self.boards)
        return board

    @property
    def started(self) -> bool:
        return self.referee is not None

    # turns -------------------------------------------------------------

    def opponent(self, player: str) -> str:
        return self.players[1] if player == self.players[0] else self.players[0]

    def next_player(self) -> str:
        if self.active is None:
            return self.first_player
        return self.opponent(self.active)

    def begin_activation(self, player: str) -> int:
        if not self.started:
            raise RuntimeError("both boards must be placed before play")
        if self.over:
            raise RuntimeError("the game is over")
        self.activation += 1
        self.active = player
        self._fired_this_activation = False
        return self.activation

    def end_activation(self) -> None:
        player = self.active
        self._idle_streak = 0 if self._fired_this_activation else self._idle_streak + 1

        if self._final_activation_for is not None and player == self._final_activation_for:
            self.end_reason = EndReason.FLEET_SUNK
        elif self.winner is not None and self._final_activation_for is None:
            self._final_activation_for = self.opponent(self.winner)
        if self.end_reason is None and self.activation >= self.max_activations:
            self.end_reason = EndReason.MAX_ACTIVATIONS
        if self.end_reason is None and self._idle_streak >= self.stall_limit:
            self.end_reason = EndReason.STALLED

    def abort(self, reason: EndReason) -> None:
        self.end_reason = reason

    @property
    def over(self) -> bool:
        return self.end_reason is not None

    # moves -------------------------------------------------------------

    def submit_fire(self, player: str, coord_text: str) -> FireDecision:
        rejection = self._check_move(player)
        if rejection is None and self.winner is not None:
            rejection = Rejection.GAME_DECIDED
        coord = None
        if rejection is None:
            try:
                coord = Coord.parse(coord_text, self.size)
            except ValueError:
                rejection = Rejection.OFF_BOARD
        if rejection is None and self._fired_this_activation:
            rejection = Rejection.EXTRA_FIRE
        if rejection is not None:
            return FireDecision(coord_text, None, rejection)

        shot = self.referee.record_shot(player, coord, self.activation)
        self._fired_this_activation = True
        self.shots_fired[player] += 1
        if self.winner is None and self.referee.fleet_sunk(shot.defender):
            self.winner = player
        return FireDecision(coord_text, shot, None)

    def submit_result(self, player: str, claim: ResultClaim) -> ResultDecision:
        rejection = self._check_move(player)
        coord = None
        if rejection is None:
            try:
                coord = Coord.parse(claim.coord_text, self.size)
            except ValueError:
                rejection = Rejection.OFF_BOARD
        if rejection is not None:
            return ResultDecision(claim.coord_text, None, rejection)
        record = self.referee.record_announcement(
            player, coord, claim.outcome, claim.ship_text, self.activation
        )
        return ResultDecision(claim.coord_text, record, None)

    def _check_move(self, player: str) -> Rejection | None:
        if not self.started:
            return Rejection.NOT_STARTED
        if player != self.active:
            return Rejection.NOT_ACTIVE
        return None
