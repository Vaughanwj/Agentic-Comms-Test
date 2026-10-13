"""Run N games for one cell (one transport x one condition).

    python -m harness.run_cell --transport socket --condition A
    python -m harness.run_cell --transport file --condition C --games 2 --sandbox local --agent scripted
"""
from __future__ import annotations

import argparse
import sys

from harness.cells import new_run_id, run_games, write_manifest
from harness.config import Sandbox, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run N games for one cell.")
    parser.add_argument("--config", default="config/phase1.toml")
    parser.add_argument("--transport", choices=["socket", "file"], required=True)
    parser.add_argument("--condition", choices=["A", "B", "C"], required=True)
    parser.add_argument("--games", type=int, help="games to play (default: games_per_cell)")
    parser.add_argument("--sandbox", choices=["docker", "local"], help="override sandbox.mode")
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--parallel", type=int, default=1, help="games to play at once (default 1)")
    parser.add_argument("--resume", action="store_true",
                        help="continue an existing --run-id: skip finished games, replay aborted ones")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    sandbox = Sandbox(args.sandbox, config.sandbox.image, config.sandbox.network, config.sandbox.proxy) \
        if args.sandbox else None
    config = config.with_overrides(transport=args.transport, condition=args.condition,
                                   games_per_cell=args.games, sandbox=sandbox)
    if args.resume and not args.run_id:
        parser.error("--resume needs the --run-id of the run to continue")
    run_id = args.run_id or new_run_id()
    manifest = write_manifest(config, run_id, args.agent, [config.cell])
    print(f"Run {run_id}: {config.games_per_cell} games of {config.cell} ({args.agent}, {config.sandbox.mode})")
    print(f"Manifest: {manifest}")

    plan = [(config, i) for i in range(config.games_per_cell)]
    results = run_games(plan, run_id, args.agent, args.parallel, args.resume)
    failed = sum(1 for r in results if r.error_type)
    print(f"Done: {len(results)} games, {failed} aborted. Records in {manifest.parent}")
    return 1 if failed or len(results) < len(plan) else 0


if __name__ == "__main__":
    sys.exit(main())
