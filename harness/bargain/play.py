"""Play a single negotiation in this process. This is what runs inside each container.

    python -m harness.bargain.play --config config/bargain.toml --game-index 0 \
        --out records/manual/claude-haiku-5-5_public-private/game_000.jsonl

Prints the result as one JSON line. Exit code 0 means the negotiation finished,
1 means it was aborted by an error, 2 means an error that would repeat.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from harness.bargain.config import MESSAGING, load_bargain_config
from harness.bargain.runner import llm_negotiators, run_bargain_game, scripted_negotiators


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--game-index", type=int, required=True)
    parser.add_argument("--model")
    parser.add_argument("--messaging", choices=MESSAGING)
    parser.add_argument("--games", type=int)
    parser.add_argument("--agent", choices=["llm", "scripted"], default="llm")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    config = load_bargain_config(args.config).with_overrides(model=args.model, messaging=args.messaging,
                                                             games=args.games)
    out = Path(args.out)
    if out.exists():
        print(f"{out} already exists; records are append-only", file=sys.stderr)
        return 2
    if args.agent == "llm":
        factory = llm_negotiators(config, os.environ.get("BATTLESHIP_EGRESS_PROXY") or None,
                                  os.environ.get("ANTHROPIC_WORKSPACE_ID") or None)
    else:
        factory = scripted_negotiators(config)
    result = run_bargain_game(config, args.game_index, out, factory)
    print(json.dumps(dataclasses.asdict(result)), flush=True)
    if result.fatal:
        return 2
    return 1 if result.error_type else 0


if __name__ == "__main__":
    sys.exit(main())
