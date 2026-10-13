"""Load and validate the single experiment config file."""
from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path

from core.fleet import ShipSpec, fleet_from_config

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

TRANSPORTS = ("socket", "file")
CONDITIONS = ("A", "B", "C")


@dataclass(frozen=True)
class Limits:
    max_activations: int
    max_steps_per_activation: int
    stall_activations: int
    placement_attempts: int
    max_tokens: int


@dataclass(frozen=True)
class Sandbox:
    mode: str
    image: str
    network: str
    proxy: str | None


@dataclass(frozen=True)
class Config:
    model_a: str
    model_b: str
    effort: str
    transport: str
    condition: str
    games_per_cell: int
    board_size: int
    fleet: tuple[ShipSpec, ...]
    pressure_text: str
    first_player: str
    seed: int
    limits: Limits
    socket_host: str
    socket_port: int
    bait_dir_file: str
    bait_dir_socket: str
    records_dir: Path
    sandbox: Sandbox
    source_path: Path | None = None

    @property
    def cell(self) -> str:
        return f"{self.transport}-{self.condition}"

    @property
    def bait_dir(self) -> str:
        return self.bait_dir_file if self.transport == "file" else self.bait_dir_socket

    def model_for(self, player: str) -> str:
        return self.model_a if player == "player_a" else self.model_b

    def with_overrides(self, **changes) -> "Config":
        changes = {k: v for k, v in changes.items() if v is not None}
        cfg = dataclasses.replace(self, **changes)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.transport not in TRANSPORTS:
            raise ValueError(f"transport must be one of {TRANSPORTS}")
        if self.condition not in CONDITIONS:
            raise ValueError(f"condition must be one of {CONDITIONS}")
        if not 5 <= self.board_size <= 26:
            raise ValueError("board_size must be between 5 and 26")
        if any(s.length > self.board_size for s in self.fleet):
            raise ValueError("a ship is longer than the board")
        if sum(s.length for s in self.fleet) > self.board_size ** 2 // 2:
            raise ValueError("fleet covers more than half the board")
        if self.first_player not in ("player_a", "player_b", "alternate"):
            raise ValueError("first_player must be player_a, player_b or alternate")
        if self.games_per_cell < 1:
            raise ValueError("games_per_cell must be at least 1")
        if self.sandbox.mode not in ("docker", "local"):
            raise ValueError("sandbox.mode must be docker or local")

    def snapshot(self) -> dict:
        data = dataclasses.asdict(self)
        data["records_dir"] = str(self.records_dir)
        data["source_path"] = str(self.source_path) if self.source_path else None
        return data


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    limits = raw.get("limits", {})
    sock = raw.get("socket", {})
    workspace = raw.get("workspace", {})
    output = raw.get("output", {})
    sandbox = raw.get("sandbox", {})
    cfg = Config(
        model_a=raw["model_a"],
        model_b=raw["model_b"],
        effort=raw.get("effort", "medium"),
        transport=raw["transport"],
        condition=str(raw["condition"]).upper(),
        games_per_cell=int(raw["games_per_cell"]),
        board_size=int(raw["board_size"]),
        fleet=fleet_from_config(raw["fleet"]),
        pressure_text=raw["pressure_text"],
        first_player=raw.get("first_player", "alternate"),
        seed=int(raw.get("seed", 0)),
        limits=Limits(
            max_activations=int(limits.get("max_activations", 210)),
            max_steps_per_activation=int(limits.get("max_steps_per_activation", 12)),
            stall_activations=int(limits.get("stall_activations", 6)),
            placement_attempts=int(limits.get("placement_attempts", 3)),
            max_tokens=int(limits.get("max_tokens", 32000)),
        ),
        socket_host=sock.get("host", "127.0.0.1"),
        socket_port=int(sock.get("port", 47001)),
        bait_dir_file=workspace.get("bait_dir_file", "/shared/boards"),
        bait_dir_socket=workspace.get("bait_dir_socket", "/game/boards"),
        records_dir=Path(output.get("records_dir", "records")).resolve(),  # relative to the working directory
        sandbox=Sandbox(
            mode=sandbox.get("mode", "docker"),
            image=sandbox.get("image", "battleship-experiment:phase1"),
            network=sandbox.get("network", "battleship_sandbox"),
            proxy=sandbox.get("proxy") or None,
        ),
        source_path=path.resolve(),
    )
    cfg.validate()
    return cfg
