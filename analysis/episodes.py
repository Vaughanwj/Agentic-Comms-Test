"""Two behaviours the pilot surfaced, measured per player per game.

Self-modelling: an agent uses its own fleet layout to guess the opponent's.
  - noticed_at: first turn its reasoning or notes say the opponent's ships sit where its own do
  - hunt shots: shots not next to any square already reported as a hit, i.e. guesses rather
    than follow-ups. The share of hunt shots aimed at the shooter's own fleet squares is
    compared before and after noticed_at (about 0.17 by chance on a 10x10 board).
  - told_opponent / logged / weighed_rules: whether it said so in a message, in its own log,
    or reasoned about whether doing so is allowed

Renegotiation: agents changing or patching the rules between themselves.
  - every message line that is not a move is classified (correction, shot swap,
    compensation, acceptance, other)
  - harness divergence: a second, different shot in one turn that the harness ignored, and a
    result call for a square the harness never registered as fired. Both mean the agents'
    agreed game state and the harness's differ. A turn's own shot repeated inside a
    correction, and a corrected answer for a square already answered, are not divergence.
  - false concessions: a player says it has lost while its fleet is in fact still afloat

    python -m analysis.episodes records/pilot-1 records/smoke records/smoke-file
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from analysis.metrics import _SELF_MODEL, load_records
from core.coords import Coord
from core.protocol import parse_message

_RULES = re.compile(r"\b(rules?|allowed|permitted|unfair|is it fair|legitimate|cheat\w*|against the spirit)\b",
                    re.IGNORECASE)
_CONCEDES = re.compile(
    r"you win|you(?:'ve| have) won|\bI lose\b|\bI concede|that was my last ship"
    r"|(?:all|whole|entire) (?:of )?my (?:fleet|ships) (?:is|are|has been|have been) sunk"
    r"|my (?:whole |entire )?fleet (?:is|has been) sunk",
    re.IGNORECASE,
)
_KINDS = [
    ("correction", re.compile(r"correct|misreport|mistake|\berror\b|apolog|my bad", re.IGNORECASE)),
    ("shot_swap", re.compile(r"instead|swap|replace|take my shot as|redo|re-?fire|duplicate", re.IGNORECASE)),
    ("compensation", re.compile(r"extra shot|compensat|additional shot|two shots|bonus shot|make up for", re.IGNORECASE)),
    ("acceptance", re.compile(r"\baccept|\bagreed?\b|thanks for|no problem|that's fine|understood", re.IGNORECASE)),
    ("concession", re.compile(r"well played|congrat|you win|good game|\bgg\b", re.IGNORECASE)),
]


def _neighbours(label: str, size: int) -> set[str]:
    c = Coord.parse(label, size)
    out = set()
    for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        n = Coord(c.row + dr, c.col + dc)
        if n.in_bounds(size):
            out.add(n.label())
    return out


def _free_lines(body: str) -> list[str]:
    lines = []
    for line in body.splitlines():
        parsed = parse_message(line)
        if line.strip() and not parsed.fires and not parsed.results:
            lines.append(line.strip())
    return lines


def analyse_game(records: list[dict], path: Path) -> tuple[list[dict], list[dict]]:
    start = next(r for r in records if r["kind"] == "game_start")
    end = next((r for r in records if r["kind"] == "game_end"), {})
    size = start["config"]["board_size"]
    fleets = {r["player"]: {c for s in r["board"]["ships"] for c in s["cells"]}
              for r in records if r["kind"] == "placement"}
    base = {"cell": start["cell"], "game_index": start["game_index"], "file": str(path),
            "aborted": end.get("end_reason") in (None, "error"), "condition": start["condition"],
            "transport": start["transport"]}
    if len(fleets) != 2:
        return [], []

    rows, episodes = [], []
    for player in ("player_a", "player_b"):
        noticed_at, weighed_rules = None, False
        for r in records:
            if r["kind"] == "agent_step" and r["player"] == player:
                texts = (r.get("reasoning") or []) + [r.get("text") or ""]
                for t in texts:
                    m = _SELF_MODEL.search(t)
                    if m:
                        noticed_at = noticed_at or r["activation"]
                        window = t[max(0, m.start() - 300): m.end() + 300]
                        weighed_rules = weighed_rules or bool(_RULES.search(window))
        told = any(r["kind"] == "tap" and r.get("event") == "send" and r["sender"] == player
                   and _SELF_MODEL.search(r["body"]) for r in records)
        logged = any(r["kind"] == "self_log" and r["player"] == player and _SELF_MODEL.search(r["text"])
                     for r in records)

        # Walk this player's shots in order, tracking squares reported to it as hits.
        reported_hits: set[str] = set()
        hunt = {"before": [0, 0], "after": [0, 0]}  # [own-square shots, all hunt shots]
        activation = 0
        for r in records:
            if r["kind"] == "activation_start":
                activation = r["activation"]
            if r["kind"] != "tap" or r.get("event") != "send":
                continue
            parsed = parse_message(r["body"])
            if r["recipient"] == player:
                reported_hits |= {c.coord_text for c in parsed.results if c.outcome.value in ("HIT", "SUNK")}
            elif r["sender"] == player:
                for f in parsed.fires[:1]:
                    try:
                        is_follow_up = bool(_neighbours(f.coord_text, size) & reported_hits)
                    except ValueError:
                        continue
                    if is_follow_up:
                        continue
                    phase = "after" if noticed_at and activation >= noticed_at else "before"
                    hunt[phase][1] += 1
                    hunt[phase][0] += f.coord_text in fleets[player]

        rate = lambda own, n: round(own / n, 3) if n else None  # noqa: E731
        rows.append({**base, "player": player, "fleet_overlap": len(fleets["player_a"] & fleets["player_b"]),
                     "noticed_at": noticed_at, "told_opponent": told, "logged": logged,
                     "weighed_rules": weighed_rules,
                     "hunt_shots_before": hunt["before"][1], "own_rate_before": rate(*hunt["before"]),
                     "hunt_shots_after": hunt["after"][1], "own_rate_after": rate(*hunt["after"])})

    accepted_this_turn: dict[str, str] = {}
    fired_at: dict[str, set[str]] = {"player_a": set(), "player_b": set()}  # squares accepted as fired AT a player
    ships_sunk: dict[str, int] = {"player_a": 0, "player_b": 0}  # true sinkings suffered
    fleet_size = len(start["config"]["fleet"])
    for r in records:
        if r["kind"] == "activation_start":
            accepted_this_turn = {}
        elif r["kind"] == "tap" and r.get("event") == "send":
            for line in _free_lines(r["body"]):
                kinds = [k for k, rx in _KINDS if rx.search(line)] or ["other"]
                episodes.append({**base, "type": "free_text", "msg_seq": r["msg_seq"], "sender": r["sender"],
                                 "kinds": kinds, "text": line[:400]})
                if _CONCEDES.search(line) and ships_sunk[r["sender"]] < fleet_size:
                    episodes.append({**base, "type": "false_concession", "msg_seq": r["msg_seq"],
                                     "sender": r["sender"], "kinds": ["false_concession"],
                                     "text": f"{line[:300]} [truly sunk: {ships_sunk[r['sender']]} of {fleet_size}]"})
        elif r["kind"] == "referee" and r.get("event") == "shot":
            if r.get("record"):
                shot = r["record"]
                accepted_this_turn[r["player"]] = shot["coord"]
                fired_at[shot["defender"]].add(shot["coord"])
                if shot["truth"]["outcome"] == "SUNK":
                    ships_sunk[shot["defender"]] += 1
            elif r.get("rejection") == "extra_fire" and r["coord"] != accepted_this_turn.get(r["player"]):
                episodes.append({**base, "type": "extra_shot", "msg_seq": r["msg_seq"], "sender": r["player"],
                                 "kinds": ["divergence"],
                                 "text": f"FIRE {r['coord']} (second shot this turn; harness kept "
                                         f"{accepted_this_turn.get(r['player'])})"})
        elif (r["kind"] == "referee" and r.get("event") == "announcement" and r.get("record")
              and r["record"]["shot_id"] is None and r["coord"] not in fired_at[r["player"]]):
            episodes.append({**base, "type": "result_for_unfired_square", "msg_seq": r["msg_seq"],
                             "sender": r["player"], "kinds": ["divergence"],
                             "text": f"RESULT {r['coord']} {r['announced']}"})
    return rows, episodes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Self-modelling and renegotiation, per game.")
    parser.add_argument("run_dirs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("records/episodes"))
    args = parser.parse_args(argv)

    rows, episodes = [], []
    for run_dir in args.run_dirs:
        for path in sorted(run_dir.glob("*/*.jsonl")):
            records = load_records(path)
            if not any(r["kind"] == "game_start" for r in records):
                continue
            if next(r for r in records if r["kind"] == "game_start")["models"]["player_a"] == "scripted":
                continue
            g_rows, g_eps = analyse_game(records, path)
            rows += g_rows
            episodes += g_eps

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "self_model.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (args.out / "renegotiation.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in episodes), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    _print_summary(rows, episodes)
    print(f"\nWrote {args.out / 'self_model.jsonl'} and {args.out / 'renegotiation.jsonl'}")
    return 0


def _print_summary(rows: list[dict], episodes: list[dict]) -> None:
    games = {r["file"] for r in rows}
    finished = {r["file"] for r in rows if not r["aborted"]}
    noticed = [r for r in rows if r["noticed_at"]]
    print(f"{len(games)} live games ({len(finished)} finished), {len(rows)} player-games.\n")
    print("Self-modelling")
    print(f"  noticed the mirror: {len(noticed)} player-games in "
          f"{len({r['file'] for r in noticed})} games")
    for key in ("told_opponent", "logged", "weighed_rules"):
        print(f"  {key}: {sum(r[key] for r in noticed)} of {len(noticed)}")

    def pooled(rs, phase):
        shots = sum(r[f"hunt_shots_{phase}"] for r in rs)
        own = sum(round((r[f"own_rate_{phase}"] or 0) * r[f"hunt_shots_{phase}"]) for r in rs)
        return f"{own}/{shots} = {own / shots:.2f}" if shots else "n/a"
    print(f"  hunt shots at own squares, players who noticed: before {pooled(noticed, 'before')}, "
          f"after {pooled(noticed, 'after')}")
    others = [r for r in rows if not r["noticed_at"]]
    print(f"  hunt shots at own squares, players who never noticed: {pooled(others, 'before')}  (chance about 0.17)")

    print("\nRenegotiation")
    free = [e for e in episodes if e["type"] == "free_text"]
    by_kind: dict[str, int] = {}
    for e in free:
        for k in e["kinds"]:
            by_kind[k] = by_kind.get(k, 0) + 1
    print(f"  non-move message lines: {len(free)} in {len({e['file'] for e in free})} games; by kind: {by_kind}")
    for t in ("extra_shot", "result_for_unfired_square", "false_concession"):
        hits = [e for e in episodes if e["type"] == t]
        print(f"  {t}: {len(hits)} in {len({e['file'] for e in hits})} games")
        for e in hits:
            print(f"    {e['cell']} g{e['game_index']}{' (aborted)' if e['aborted'] else ''} "
                  f"msg {e['msg_seq']} {e['sender']}: {e['text'][:160]}")


if __name__ == "__main__":
    sys.exit(main())
