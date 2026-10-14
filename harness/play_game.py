"""Play a single game in this process. This is what runs inside each container.

    python -m harness.play_game --config config/phase1.toml --transport file --condition B \
        --game-index 0 --out records/manual/file-B/game_000.jsonl

Prints the result as one JSON line. Exit code 0 means the game finished,
1 means it was aborted by an error, 2 means an error that would repeat.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from harness.agents import factory_for
from harness.config import load_config
from harness.game_runner import run_game


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--transport", choices=["socket", "file"])
    parser.add_argument("--condition", choices=["A", "B", "C"])
    parser.add_argument("--games-per-cell", type=int)
    parser.add_argument("--game-index", type=int, required=True)
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    config = load_config(args.config).with_overrides(
        transport=args.transport, condition=args.condition, games_per_cell=args.games_per_cell
    )
    out = Path(args.out)
    if out.exists():
        print(f"{out} already exists; records are append-only", file=sys.stderr)
        return 2
    result = run_game(config, args.game_index, out, factory_for(args.agent, config))
    print(json.dumps(dataclasses.asdict(result)), flush=True)
    if result.fatal:
        return 2
    return 1 if result.error_type else 0


if __name__ == "__main__":
    sys.exit(main())
