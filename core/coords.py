"""Board coordinates.

Rows are letters (A is the top row) and columns are numbers starting at 1,
so "B7" is the second row, seventh column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_COORD_RE = re.compile(r"^\s*([A-Za-z])\s*-?\s*(\d{1,2})\s*$")


@dataclass(frozen=True, order=True)
class Coord:
    row: int  # 0-based
    col: int  # 0-based

    @classmethod
    def parse(cls, text: str, size: int) -> "Coord":
        match = _COORD_RE.match(text)
        if not match:
            raise ValueError(f"not a coordinate: {text!r}")
        coord = cls(ROW_LETTERS.index(match.group(1).upper()), int(match.group(2)) - 1)
        if not coord.in_bounds(size):
            raise ValueError(f"coordinate off the board: {text!r}")
        return coord

    def in_bounds(self, size: int) -> bool:
        return 0 <= self.row < size and 0 <= self.col < size

    def label(self) -> str:
        return f"{ROW_LETTERS[self.row]}{self.col + 1}"

    def __str__(self) -> str:
        return self.label()
