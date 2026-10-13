"""Run N negotiations, each in its own container by default.

    python -m harness.bargain.run --games 10 --parallel 4
    python -m harness.bargain.run --games 5 --model claude-opus-5-5 --parallel 4
    python -m harness.bargain.run --games 2 --sandbox local --agent scripted

The same game index draws the same fallbacks, proposer order and turn order for
every model and messaging setting, so runs can be compared game by game.
"""
from __future__ import annotations

import argparse
import os
import sys

from harness.bargain.config import MESSAGING, BargainConfig, load_bargain_config
from harness.bargain.runner import BargainResult, llm_negotiators, run_bargain_game, scripted_negotiators
from harness.cells import claim_record, new_run_id, run_games, run_in_container, write_manifest
from harness.config import Sandbox


def play(config: BargainConfig, game_index: int, run_id: str, agent_kind: str, resume: bool = False) -> BargainResult:
    out = claim_record(config, run_id, game_index, resume)
    if config.sandbox.mode == "local":
        if agent_kind == "llm":
            factory = llm_negotiators(config, os.environ.get("BATTLESHIP_EGRESS_PROXY") or None,
                                      os.environ.get("ANTHROPIC_WORKSPACE_ID") or None)
        else:
            factory = scripted_negotiators(config)
        return run_bargain_game(config, game_index, out, factory)
    data = run_in_container(config, run_id, out, "harness.bargain.play", [
        "--game-index", str(game_index), "--agent", agent_kind, "--model", config.model,
        "--messaging", config.messaging, "--games", str(config.games),
    ])
    if isinstance(data, str):
        return BargainResult(config.cell, game_index, "error", None, None, "DockerError", data)
    return BargainResult(**data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run N negotiations.")
    parser.add_argument("--config", default="config/bargain.toml")
    parser.add_argument("--games", type=int, help="number of negotiations (default: games in the config)")
    parser.add_argument("--model", help="override the config's model")
    parser.add_argument("--messaging", choices=MESSAGING, help="override the config's messaging")
    parser.add_argument("--sandbox", choices=["docker", "local"], help="override sandbox.mode")
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--parallel", type=int, default=1, help="negotiations to run at once (default 1)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--resume", action="store_true",
                        help="continue an existing --run-id: skip finished games, replay aborted ones")
    args = parser.parse_args(argv)

    config = load_bargain_config(args.config)
    sandbox = Sandbox(args.sandbox, config.sandbox.image, config.sandbox.network, config.sandbox.proxy) \
        if args.sandbox else None
    config = config.with_overrides(games=args.games, model=args.model, messaging=args.messaging, sandbox=sandbox)
    if args.resume and not args.run_id:
        parser.error("--resume needs the --run-id of the run to continue")
    run_id = args.run_id or new_run_id()
    manifest = write_manifest(config, run_id, args.agent, [config.cell])
    print(f"Run {run_id}: {config.games} negotiations, {config.model}, {config.messaging} "
          f"({args.agent}, {config.sandbox.mode})")
    print(f"Manifest: {manifest}")

    plan = [(config, i) for i in range(config.games)]
    results = run_games(plan, run_id, args.agent, args.parallel, args.resume, play_fn=play)
    failed = sum(1 for r in results if r.error_type)
    print(f"Done: {len(results)} negotiations, {failed} aborted. Records in {manifest.parent}")
    return 1 if failed or len(results) < len(plan) else 0


if __name__ == "__main__":
    sys.exit(main())
