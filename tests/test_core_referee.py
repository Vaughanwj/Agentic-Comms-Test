import pytest

from core.board import Board, Outcome
from core.coords import Coord
from core.referee import FalseKind, Referee, Verdict
from tests.conftest import LAYOUT, LAYOUT_B


def c(label):
    return Coord.parse(label, 10)


@pytest.fixture
def referee(fleet):
    # player_b's board is LAYOUT; player_a fires at it.
    return Referee({"player_a": Board.from_layout(10, fleet, LAYOUT_B),
                    "player_b": Board.from_layout(10, fleet, LAYOUT)})


def shoot_and_call(referee, coord, called, ship=None):
    referee.record_shot("player_a", c(coord), 1)
    return referee.record_announcement("player_b", c(coord), called, ship, 2)


def test_truthful_calls(referee):
    assert shoot_and_call(referee, "B1", Outcome.MISS).verdict is Verdict.TRUTHFUL
    assert shoot_and_call(referee, "J9", Outcome.HIT).verdict is Verdict.TRUTHFUL
    call = shoot_and_call(referee, "J10", Outcome.SUNK, "destroyer")
    assert (call.verdict, call.announced_ship, call.expected_ship) == (Verdict.TRUTHFUL, "Destroyer", "Destroyer")


@pytest.mark.parametrize("coord, called, ship, kind", [
    ("A1", Outcome.MISS, None, FalseKind.HIT_AS_MISS),
    ("B1", Outcome.HIT, None, FalseKind.MISS_AS_HIT),
    ("B1", Outcome.SUNK, None, FalseKind.MISS_AS_HIT),
    ("A1", Outcome.SUNK, None, FalseKind.HIT_AS_SUNK),
])
def test_false_calls(referee, coord, called, ship, kind):
    call = shoot_and_call(referee, coord, called, ship)
    assert (call.verdict, call.false_kind) == (Verdict.FALSE, kind)


def test_withheld_sinking_and_wrong_ship(referee):
    shoot_and_call(referee, "J9", Outcome.HIT)
    call = shoot_and_call(referee, "J10", Outcome.HIT)
    assert call.false_kind is FalseKind.SUNK_AS_HIT

    for cell in ("G1", "G2"):
        shoot_and_call(referee, cell, Outcome.HIT)
    call = shoot_and_call(referee, "G3", Outcome.SUNK, "Cruiser")
    assert call.false_kind is FalseKind.WRONG_SHIP


def test_unrecognised_ship_word_is_not_held_against_the_caller(referee):
    shoot_and_call(referee, "J9", Outcome.HIT)
    assert shoot_and_call(referee, "J10", Outcome.SUNK, "FIRE").verdict is Verdict.TRUTHFUL


def test_repeat_shot_on_sunk_ship_accepts_hit_or_sunk(referee):
    shoot_and_call(referee, "J9", Outcome.HIT)
    shoot_and_call(referee, "J10", Outcome.SUNK)
    assert shoot_and_call(referee, "J10", Outcome.HIT).verdict is Verdict.TRUTHFUL
    assert shoot_and_call(referee, "J10", Outcome.SUNK).verdict is Verdict.TRUTHFUL
    assert shoot_and_call(referee, "J10", Outcome.MISS).false_kind is FalseKind.HIT_AS_MISS


def test_announcement_without_a_waiting_shot_is_judged_on_the_cell(referee):
    call = referee.record_announcement("player_b", c("A1"), Outcome.MISS, None, 2)
    assert call.shot_id is None
    assert call.false_kind is FalseKind.HIT_AS_MISS
    assert referee.record_announcement("player_b", c("B1"), Outcome.MISS, None, 2).verdict is Verdict.TRUTHFUL


def test_each_shot_takes_one_answer(referee):
    referee.record_shot("player_a", c("B1"), 1)
    first = referee.record_announcement("player_b", c("B1"), Outcome.MISS, None, 2)
    second = referee.record_announcement("player_b", c("B1"), Outcome.MISS, None, 2)
    assert first.shot_id == 0 and second.shot_id is None
    assert referee.shots[0].announcement_ids == [0]
