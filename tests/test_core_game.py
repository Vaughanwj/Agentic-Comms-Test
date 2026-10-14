import pytest

from core.board import Outcome, PlacementError
from core.game import EndReason, Game, Rejection
from core.protocol import ResultClaim
from tests.conftest import LAYOUT, LAYOUT_B


def new_game(fleet, max_activations=500, stall_limit=4):
    game = Game(10, fleet, "player_a", max_activations, stall_limit)
    game.place("player_a", LAYOUT_B)
    game.place("player_b", LAYOUT)
    return game


def play(game, player, fire=None, result=None):
    assert game.next_player() == player
    game.begin_activation(player)
    out = []
    if result:
        out.append(game.submit_result(player, ResultClaim(result[0], result[1], None)))
    if fire:
        out.append(game.submit_fire(player, fire))
    game.end_activation()
    return out


def test_placement_rules(fleet):
    game = Game(10, fleet, "player_a", 10, 3)
    with pytest.raises(RuntimeError):
        game.begin_activation("player_a")
    game.place("player_a", LAYOUT)
    assert not game.started
    with pytest.raises(PlacementError, match="already placed"):
        game.place("player_a", LAYOUT)
    game.place("player_b", LAYOUT_B)
    assert game.started


def test_turns_alternate_and_one_shot_per_turn(fleet):
    game = new_game(fleet)
    game.begin_activation("player_a")
    assert game.submit_fire("player_a", "B1").accepted
    assert game.submit_fire("player_a", "B2").rejection is Rejection.EXTRA_FIRE
    assert game.submit_fire("player_b", "B2").rejection is Rejection.NOT_ACTIVE
    game.end_activation()
    assert game.next_player() == "player_b"
    game.begin_activation("player_b")
    assert game.submit_fire("player_b", "K1").rejection is Rejection.OFF_BOARD
    assert game.submit_fire("player_b", "A1").accepted
    assert game.shots_fired == {"player_a": 1, "player_b": 1}


def test_result_claims_reach_the_referee(fleet):
    game = new_game(fleet)
    play(game, "player_a", fire="A1")
    (decision,) = play(game, "player_b", result=("A1", Outcome.MISS))
    assert decision.announcement.false_kind.value == "hit_as_miss"


def test_win_gives_the_loser_one_last_turn(fleet):
    game = new_game(fleet)
    targets = [cell.label() for name in game.boards["player_b"].ship_names
               for cell in game.boards["player_b"].ship_cells(name)]
    # Water on LAYOUT_B: all of row I, and J3 to J10.
    misses = iter([f"I{col}" for col in range(1, 11)] + [f"J{col}" for col in range(3, 11)])
    for i, target in enumerate(targets):
        play(game, "player_a", fire=target)
        if i < len(targets) - 1:
            play(game, "player_b", fire=next(misses))
    assert game.winner == "player_a" and not game.over
    (late_shot,) = play(game, "player_b", fire=next(misses))
    assert late_shot.rejection is Rejection.GAME_DECIDED
    assert game.end_reason is EndReason.FLEET_SUNK


def test_stall_and_activation_cap(fleet):
    game = new_game(fleet, stall_limit=3)
    for player in ("player_a", "player_b", "player_a"):
        play(game, player)
    assert game.end_reason is EndReason.STALLED

    game = new_game(fleet, max_activations=2)
    play(game, "player_a", fire="B1")
    play(game, "player_b", fire="B1")
    assert game.end_reason is EndReason.MAX_ACTIVATIONS
