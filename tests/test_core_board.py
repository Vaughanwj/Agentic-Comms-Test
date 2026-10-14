import pytest

from core.board import Board, FleetState, Outcome, PlacementError
from core.coords import Coord
from tests.conftest import LAYOUT


def c(label):
    return Coord.parse(label, 10)


class TestCoord:
    def test_parse_and_label(self):
        assert c("B7") == Coord(1, 6)
        assert c("b7").label() == "B7"
        assert c("J10") == Coord(9, 9)
        assert c(" A-1 ") == Coord(0, 0)

    @pytest.mark.parametrize("text", ["K1", "A0", "A11", "7B", "", "AA1"])
    def test_rejects_bad_coordinates(self, text):
        with pytest.raises(ValueError):
            c(text)


class TestPlacement:
    def test_valid_layout(self, fleet):
        board = Board.from_layout(10, fleet, LAYOUT)
        assert board.ship_at(c("A5")) == "Carrier"
        assert board.ship_at(c("A6")) is None
        assert board.ship_at(c("J10")) == "Destroyer"
        assert board.to_dict()["ships"][0] == {"name": "Carrier", "cells": ["A1", "A2", "A3", "A4", "A5"]}

    def test_vertical_extends_down(self, fleet):
        layout = [dict(e) for e in LAYOUT]
        layout[0] = {"name": "Carrier", "start": "F6", "orientation": "vertical"}
        assert Board.from_layout(10, fleet, layout).ship_cells("Carrier") == tuple(map(c, ["F6", "G6", "H6", "I6", "J6"]))

    @pytest.mark.parametrize("change, message", [
        ({"start": "A7"}, "off the board"),                       # Carrier A7-A11
        ({"start": "C3"}, "overlaps"),                            # Carrier on the Battleship
        ({"orientation": "diagonal"}, "orientation"),
        ({"name": "Rowboat"}, "unknown ship"),
        ({"start": "Z1"}, "Carrier"),
    ])
    def test_rejects_bad_layouts(self, fleet, change, message):
        layout = [dict(e) for e in LAYOUT]
        layout[0].update(change)
        with pytest.raises(PlacementError, match=message):
            Board.from_layout(10, fleet, layout)

    def test_rejects_missing_and_duplicate_ships(self, fleet):
        with pytest.raises(PlacementError, match="not placed"):
            Board.from_layout(10, fleet, LAYOUT[:-1])
        with pytest.raises(PlacementError, match="more than once"):
            Board.from_layout(10, fleet, LAYOUT + [{"name": "Destroyer", "start": "I1", "orientation": "horizontal"}])

    def test_ship_names_are_case_insensitive(self, fleet):
        layout = [dict(e, name=e["name"].lower()) for e in LAYOUT]
        assert Board.from_layout(10, fleet, layout).ship_at(c("A1")) == "Carrier"


class TestFleetState:
    def test_miss_hit_sunk_repeat(self, fleet):
        state = FleetState(Board.from_layout(10, fleet, LAYOUT))
        assert state.resolve(c("B1")).outcome is Outcome.MISS
        first = state.resolve(c("J9"))
        assert (first.outcome, first.ship, first.repeat) == (Outcome.HIT, "Destroyer", False)
        assert state.resolve(c("J9")).repeat is True
        sunk = state.resolve(c("J10"))
        assert (sunk.outcome, sunk.ship) == (Outcome.SUNK, "Destroyer")
        assert state.is_sunk("Destroyer") and not state.all_sunk()
        assert state.resolve(c("B1")).repeat is True

    def test_all_sunk(self, fleet):
        board = Board.from_layout(10, fleet, LAYOUT)
        state = FleetState(board)
        for name in board.ship_names:
            for cell in board.ship_cells(name):
                state.resolve(cell)
        assert state.all_sunk()
