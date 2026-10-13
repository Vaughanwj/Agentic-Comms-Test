"""Load and validate the negotiation config file."""
from __future__ import annotations

import dataclasses
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from harness.config import Sandbox

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

MESSAGING = ("public+private", "public")


@dataclass(frozen=True)
class BargainConfig:
    model: str
    effort: str
    players: int
    pot: int
    shrink: float
    max_rounds: int
    pots: int
    draft: bool
    talk_turns: int
    counter_turns: int
    fallback_min: int
    fallback_max: int
    messaging: str
    reveal_votes: bool
    honesty_rule: bool
    goal_text: str
    games: int
    seed: int
    max_steps_per_activation: int
    max_tokens: int
    socket_host: str
    socket_port: int
    records_dir: Path
    sandbox: Sandbox
    source_path: Path | None = None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f"player_{i}" for i in range(1, self.players + 1))

    @property
    def cell(self) -> str:
        """Folder name for a run's records: model and messaging, e.g. claude-haiku-5-5_public-private."""
        return f"{self.model}_{self.messaging.replace('+', '-')}"

    def draw(self, game_index: int) -> tuple[list[dict[str, int]], tuple[str, ...], random.Random]:
        """Each pot's fallbacks, the proposer order, and a generator for turn order.

        Fixed by seed and game index, so every model and setting gets the same draw.
        """
        rng = random.Random(f"{self.seed}:{game_index}")
        order = tuple(rng.sample(self.names, len(self.names)))
        fallbacks = [{p: rng.randint(self.fallback_min, self.fallback_max) for p in self.names}
                     for _ in range(self.pots)]
        return fallbacks, order, rng

    def with_overrides(self, **changes) -> "BargainConfig":
        changes = {k: v for k, v in changes.items() if v is not None}
        cfg = dataclasses.replace(self, **changes)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not 3 <= self.players <= 9:
            raise ValueError("players must be between 3 and 9")
        if self.messaging not in MESSAGING:
            raise ValueError(f"messaging must be one of {MESSAGING}")
        if not 0 <= self.fallback_min <= self.fallback_max:
            raise ValueError("fallback range is empty")
        if self.fallback_max * self.players > self.pot:
            raise ValueError("fallbacks could add up to more than the pot")
        if not 0 <= self.shrink < 1 or self.max_rounds < 1 or self.talk_turns < 0 or self.games < 1:
            raise ValueError("bad shrink, rounds, talk turns or games")
        if self.pots < 1 or self.counter_turns < 0:
            raise ValueError("pots must be at least 1 and counter_turns zero or more")
        if self.sandbox.mode not in ("docker", "local"):
            raise ValueError("sandbox.mode must be docker or local")

    def snapshot(self) -> dict:
        data = dataclasses.asdict(self)
        data["records_dir"] = str(self.records_dir)
        data["source_path"] = str(self.source_path) if self.source_path else None
        return data


def load_bargain_config(path: str | Path) -> BargainConfig:
    path = Path(path)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    limits, sock, output, sandbox = (raw.get(k, {}) for k in ("limits", "socket", "output", "sandbox"))
    cfg = BargainConfig(
        model=raw["model"],
        effort=raw.get("effort", "medium"),
        players=int(raw["players"]),
        pot=int(raw["pot"]),
        shrink=float(raw["shrink"]),
        max_rounds=int(raw["max_rounds"]),
        pots=int(raw.get("pots", 1)),
        draft=bool(raw.get("draft", False)),
        talk_turns=int(raw.get("talk_turns", 2)),
        counter_turns=int(raw.get("counter_turns", 1)),
        fallback_min=int(raw["fallback_min"]),
        fallback_max=int(raw["fallback_max"]),
        messaging=raw.get("messaging", "public+private"),
        reveal_votes=bool(raw.get("reveal_votes", False)),
        honesty_rule=bool(raw.get("honesty_rule", False)),
        goal_text=raw["goal_text"],
        games=int(raw.get("games", 10)),
        seed=int(raw.get("seed", 0)),
        max_steps_per_activation=int(limits.get("max_steps_per_activation", 8)),
        max_tokens=int(limits.get("max_tokens", 16000)),
        socket_host=sock.get("host", "127.0.0.1"),
        socket_port=int(sock.get("port", 47101)),
        records_dir=Path(output.get("records_dir", "records")).resolve(),
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
