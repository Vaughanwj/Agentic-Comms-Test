"""Readable transcript of one game: the messages the agents exchanged.

    python -m analysis.transcript records/<run_id>/<cell>/game_000.jsonl [--annotate] [--out file.md]

Messages come from the tap (what actually crossed the channel). With
--annotate, each result call is marked with the referee's verdict and each
shot with what it really hit.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict, deque
from pathlib import Path

from analysis.metrics import load_records, rescore_calls
from core.protocol import parse_message


def render(records: list[dict], annotate: bool = False) -> str:
    start = next(r for r in records if r["kind"] == "game_start")
    end = next((r for r in records if r["kind"] == "game_end"), {})
    fleets = {r["player"]: r["board"]["ships"] for r in records if r["kind"] == "placement"}
    # Queues in message order, so a square called or fired at twice gets the right note each time.
    calls: dict = defaultdict(deque)
    shots: dict = defaultdict(deque)
    if annotate:
        for c in rescore_calls(records, start):
            calls[(c["announcer"], c["coord"])].append(c)
        for r in records:
            if r["kind"] == "referee" and r.get("event") == "shot" and r.get("record"):
                shots[(r["player"], r["record"]["coord"])].append(r["record"]["truth"]["outcome"])

    lines = [
        f"# {start['cell']} game {start['game_index']}",
        "",
        f"- Channel: {start['transport']}; condition {start['condition']}; models: "
        + ", ".join(f"{p} {m}" for p, m in start["models"].items()),
        f"- {start['first_player']} fired first.",
        f"- Result: {end.get('end_reason', 'incomplete')}; winner {end.get('winner')}; "
        f"shots fired {end.get('shots_fired')}.",
        "",
        "## Fleets",
        "",
    ]
    for player, ships in fleets.items():
        lines.append(f"- **{player}:** " + ", ".join(f"{s['name']} {s['cells'][0]}–{s['cells'][-1]}" for s in ships))
    lines += ["", "## Messages", ""]

    activation = 0
    for r in records:
        if r["kind"] == "activation_start":
            activation = r["activation"]
        if r["kind"] != "tap" or r.get("event") != "send":
            continue
        body = r["body"].rstrip()
        if annotate:
            body = _annotate(body, r["sender"], calls, shots)
        turn = f"turn {activation}" if activation else "placement"
        lines.append(f"**{r['msg_seq']}. {r['sender']} → {r['recipient']}** ({turn})")
        lines.append("")
        lines += ["> " + line if line else ">" for line in body.splitlines()]
        lines.append("")
    return "\n".join(lines)


def _annotate(body: str, sender: str, calls: dict, shots: dict) -> str:
    out = []
    for line in body.splitlines():
        parsed = parse_message(line)
        notes = []
        for claim in parsed.results:
            queue = calls.get((sender, claim.coord_text))
            call = queue.popleft() if queue else None
            if call:
                notes.append("✓ true" if call["verdict"] == "truthful" else f"✗ FALSE ({call['false_kind']})")
        for fire in parsed.fires:
            queue = shots.get((sender, fire.coord_text))
            truth = queue.popleft() if queue else None
            if truth:
                notes.append(f"really {truth.lower()}")
        out.append(line + (f"   _[{'; '.join(notes)}]_" if notes else ""))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Readable transcript of one game's messages.")
    parser.add_argument("game", type=Path)
    parser.add_argument("--annotate", action="store_true", help="mark result calls and shots with ground truth")
    parser.add_argument("--out", type=Path, help="write markdown here instead of printing it")
    args = parser.parse_args(argv)
    text = render(load_records(args.game), args.annotate)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
