"""Extract shots and result calls from free-text agent messages.

Agents are asked to put game moves on their own lines:

    RESULT B7 HIT
    RESULT B7 MISS
    RESULT B7 SUNK Destroyer
    FIRE C4

Keywords must be upper case unless they open a line, which keeps chat such as
"ready to fire c4?" from counting as a shot. Everything else in a message is
free text and ignored here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.board import Outcome

_COORD = r"([A-Za-z])[ \t]*-?[ \t]*(\d{1,2})(?!\d)"
_FIRE_RE = re.compile(r"(?<![A-Za-z])FIRE(?![A-Za-z])[ \t]*[:\-]?[ \t]*" + _COORD)
_RESULT_RE = re.compile(
    r"(?<![A-Za-z])RESULT(?![A-Za-z])[ \t]*[:\-]?[ \t]*" + _COORD
    + r"[ \t]*[:\-,]?[ \t]*(HIT|MISS|SUNK)(?![A-Za-z])"
    + r"(?:[ \t]*[:\-,(]?[ \t]*([A-Za-z]+))?",
    re.IGNORECASE,
)
_LEADING_NOISE = re.compile(r"^[\s>*#`_\-•]+")
_LEADING_KEYWORD = re.compile(r"^(fire|result)(?![A-Za-z])", re.IGNORECASE)


@dataclass(frozen=True)
class FireClaim:
    coord_text: str


@dataclass(frozen=True)
class ResultClaim:
    coord_text: str
    outcome: Outcome
    ship_text: str | None  # the word after SUNK, if any; may not be a real ship name


@dataclass(frozen=True)
class ParsedMessage:
    fires: tuple[FireClaim, ...]
    results: tuple[ResultClaim, ...]


def parse_message(text: str) -> ParsedMessage:
    fires: list[FireClaim] = []
    results: list[ResultClaim] = []
    for raw_line in text.splitlines():
        line = _LEADING_NOISE.sub("", raw_line)
        line = _LEADING_KEYWORD.sub(lambda m: m.group(1).upper(), line)
        for m in _FIRE_RE.finditer(line):
            fires.append(FireClaim(f"{m.group(1).upper()}{int(m.group(2))}"))
        for m in _RESULT_RE.finditer(line):
            if not m.group(0).lstrip().startswith("RESULT"):
                continue  # IGNORECASE is for the outcome word; the keyword itself must be upper case
            outcome = Outcome(m.group(3).upper())
            ship = m.group(4) if outcome is Outcome.SUNK else None
            results.append(ResultClaim(f"{m.group(1).upper()}{int(m.group(2))}", outcome, ship))
    return ParsedMessage(tuple(fires), tuple(results))
