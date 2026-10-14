from pathlib import Path

import pytest

from core.fleet import STANDARD_FLEET
from harness.config import load_config

REPO = Path(__file__).resolve().parent.parent

# A legal layout for the standard fleet on a 10x10 board:
#   Carrier A1-A5, Battleship C1-C4, Cruiser E1-E3, Submarine G1-G3, Destroyer J9-J10
LAYOUT = [
    {"name": "Carrier", "start": "A1", "orientation": "horizontal"},
    {"name": "Battleship", "start": "C1", "orientation": "horizontal"},
    {"name": "Cruiser", "start": "E1", "orientation": "horizontal"},
    {"name": "Submarine", "start": "G1", "orientation": "horizontal"},
    {"name": "Destroyer", "start": "J9", "orientation": "horizontal"},
]
# Same ships, different place.
LAYOUT_B = [
    {"name": "Carrier", "start": "B10", "orientation": "vertical"},
    {"name": "Battleship", "start": "A8", "orientation": "vertical"},
    {"name": "Cruiser", "start": "H5", "orientation": "horizontal"},
    {"name": "Submarine", "start": "D4", "orientation": "vertical"},
    {"name": "Destroyer", "start": "J1", "orientation": "horizontal"},
]


@pytest.fixture
def fleet():
    return STANDARD_FLEET


@pytest.fixture
def config():
    return load_config(REPO / "config" / "phase1.toml").with_overrides(socket_port=0)
