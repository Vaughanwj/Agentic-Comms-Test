"""Ship specifications."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ShipSpec:
    name: str
    length: int


STANDARD_FLEET: tuple[ShipSpec, ...] = (
    ShipSpec("Carrier", 5),
    ShipSpec("Battleship", 4),
    ShipSpec("Cruiser", 3),
    ShipSpec("Submarine", 3),
    ShipSpec("Destroyer", 2),
)


def fleet_from_config(entries: Iterable[Mapping]) -> tuple[ShipSpec, ...]:
    fleet = tuple(ShipSpec(str(e["name"]), int(e["length"])) for e in entries)
    names = [s.name.lower() for s in fleet]
    if not fleet:
        raise ValueError("fleet is empty")
    if len(set(names)) != len(names):
        raise ValueError("ship names must be unique")
    if any(s.length < 1 for s in fleet):
        raise ValueError("ship lengths must be positive")
    return fleet
