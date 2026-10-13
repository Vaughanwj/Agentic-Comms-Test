"""Run all six cells (2 transports x 3 conditions).

Games are interleaved across cells, in a seeded random order within each
round, so slow drift in the API or the environment does not line up with
one cell.

    python -m harness.run_all
    python -m harness.run_all --games 1 --sandbox local --agent scripted
"""
from __future__ import annotations

import argparse
import random
import sys

from harness.cells import new_run_id, run_games, write_manifest
from harness.config import CONDITIONS, TRANSPORTS, Sandbox, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all six cells.")
    parser.add_argument("--config", default="config/phase1.toml")
    parser.add_argument("--games", type=int, help="games per cell (default: games_per_cell)")
    parser.add_argument("--sandbox", choices=["docker", "local"], help="override sandbox.mode")
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    base = load_config(args.config)
    sandbox = Sandbox(args.sandbox, base.sandbox.image, base.sandbox.network, base.sandbox.proxy) \
        if args.sandbox else None
    base = base.with_overrides(games_per_cell=args.games, sandbox=sandbox)
    cells = [base.with_overrides(transport=t, condition=c) for t in TRANSPORTS for c in CONDITIONS]

    rng = random.Random(base.seed)
    plan = []
    for game_index in range(base.games_per_cell):
        round_ = list(cells)
        rng.shuffle(round_)
        plan.extend((cell, game_index) for cell in round_)

    run_id = args.run_id or new_run_id()
    manifest = write_manifest(base, run_id, args.agent, [c.cell for c in cells])
    print(f"Run {run_id}: {len(plan)} games across {len(cells)} cells ({args.agent}, {base.sandbox.mode})")
    print(f"Manifest: {manifest}")

    results = run_games(plan, run_id, args.agent)
    failed = sum(1 for r in results if r.error_type)
    print(f"Done: {len(results)} games, {failed} aborted. Records in {manifest.parent}")
    return 1 if failed or len(results) < len(plan) else 0


if __name__ == "__main__":
    sys.exit(main())
