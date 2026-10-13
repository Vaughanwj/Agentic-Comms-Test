"""Ship placement, true board state, and shot resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from core.coords import Coord
from core.fleet import ShipSpec


class PlacementError(ValueError):
    """The requested layout breaks the placement rules."""


class Orientation(str, Enum):
    HORIZONTAL = "horizontal"  # extends to higher column numbers
    VERTICAL = "vertical"  # extends to later row letters


class Outcome(str, Enum):
    MISS = "MISS"
    HIT = "HIT"
    SUNK = "SUNK"


@dataclass(frozen=True)
class ShipPlacement:
    name: str
    cells: tuple[Coord, ...]


def place_ship(spec: ShipSpec, start: Coord, orientation: Orientation) -> ShipPlacement:
    if orientation is Orientation.HORIZONTAL:
        cells = tuple(Coord(start.row, start.col + i) for i in range(spec.length))
    else:
        cells = tuple(Coord(start.row + i, start.col) for i in range(spec.length))
    return ShipPlacement(spec.name, cells)


class Board:
    """A player's true ship layout. Immutable once built."""

    def __init__(self, size: int, fleet: Iterable[ShipSpec], placements: Iterable[ShipPlacement]):
        self.size = size
        self.fleet = tuple(fleet)
        specs = {s.name.lower(): s for s in self.fleet}
        self._ships: dict[str, tuple[Coord, ...]] = {}
        self._occupied: dict[Coord, str] = {}

        for placement in placements:
            spec = specs.get(placement.name.lower())
            if spec is None:
                raise PlacementError(f"unknown ship: {placement.name}")
            if spec.name in self._ships:
                raise PlacementError(f"{spec.name} placed more than once")
            if len(placement.cells) != spec.length:
                raise PlacementError(f"{spec.name} must cover {spec.length} cells")
            if not _is_straight_line(placement.cells):
                raise PlacementError(f"{spec.name} must be a straight, unbroken line")
            for cell in placement.cells:
                if not cell.in_bounds(size):
                    raise PlacementError(f"{spec.name} runs off the board")
                if cell in self._occupied:
                    raise PlacementError(f"{spec.name} overlaps {self._occupied[cell]} at {cell}")
            self._ships[spec.name] = placement.cells
            for cell in placement.cells:
                self._occupied[cell] = spec.name

        missing = [s.name for s in self.fleet if s.name not in self._ships]
        if missing:
            raise PlacementError(f"ships not placed: {', '.join(missing)}")

    @classmethod
    def from_layout(cls, size: int, fleet: Iterable[ShipSpec], layout: Iterable[Mapping]) -> "Board":
        """Build from entries like {"name": "Carrier", "start": "B3", "orientation": "vertical"}."""
        fleet = tuple(fleet)
        specs = {s.name.lower(): s for s in fleet}
        placements = []
        for entry in layout:
            name = str(entry.get("name", ""))
            spec = specs.get(name.lower())
            if spec is None:
                raise PlacementError(f"unknown ship: {name!r}")
            try:
                start = Coord.parse(str(entry.get("start", "")), size)
            except ValueError as exc:
                raise PlacementError(f"{spec.name}: {exc}") from None
            try:
                orientation = Orientation(str(entry.get("orientation", "")).lower())
            except ValueError:
                raise PlacementError(f"{spec.name}: orientation must be horizontal or vertical") from None
            placements.append(place_ship(spec, start, orientation))
        return cls(size, fleet, placements)

    def ship_at(self, coord: Coord) -> str | None:
        return self._occupied.get(coord)

    def ship_cells(self, name: str) -> tuple[Coord, ...]:
        return self._ships[name]

    @property
    def ship_names(self) -> tuple[str, ...]:
        return tuple(self._ships)

    def to_dict(self) -> dict:
        return {
            "board_size": self.size,
            "ships": [
                {"name": name, "cells": [c.label() for c in cells]}
                for name, cells in self._ships.items()
            ],
        }


def _is_straight_line(cells: tuple[Coord, ...]) -> bool:
    if len(cells) <= 1:
        return True
    rows = {c.row for c in cells}
    cols = {c.col for c in cells}
    if len(rows) == 1:
        span = sorted(c.col for c in cells)
    elif len(cols) == 1:
        span = sorted(c.row for c in cells)
    else:
        return False
    return span == list(range(span[0], span[0] + len(cells)))


@dataclass(frozen=True)
class Truth:
    """What a shot really did, according to the true board."""

    outcome: Outcome
    ship: str | None
    repeat: bool  # the cell had already been fired at


@dataclass
class FleetState:
    """Tracks damage to one player's fleet as shots land."""

    board: Board
    _shots: set[Coord] = field(default_factory=set)
    _hits: dict[str, set[Coord]] = field(default_factory=dict)

    def resolve(self, coord: Coord) -> Truth:
        repeat = coord in self._shots
        self._shots.add(coord)
        ship = self.board.ship_at(coord)
        if ship is None:
            return Truth(Outcome.MISS, None, repeat)
        if repeat:
            return Truth(Outcome.HIT, ship, True)
        hits = self._hits.setdefault(ship, set())
        hits.add(coord)
        if len(hits) == len(self.board.ship_cells(ship)):
            return Truth(Outcome.SUNK, ship, False)
        return Truth(Outcome.HIT, ship, False)

    def is_sunk(self, ship: str) -> bool:
        return len(self._hits.get(ship, ())) == len(self.board.ship_cells(ship))

    def all_sunk(self) -> bool:
        return all(self.is_sunk(name) for name in self.board.ship_names)

    def cell_outcome(self, coord: Coord) -> Outcome:
        """Static occupancy: HIT if a ship covers the cell, otherwise MISS."""
        return Outcome.MISS if self.board.ship_at(coord) is None else Outcome.HIT
