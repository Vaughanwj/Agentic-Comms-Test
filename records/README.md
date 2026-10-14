# Records

Every live run so far, as recorded. Records are append-only: nothing here has been edited by hand. Analysis outputs are regenerated from the records by the current code (`analysis/`), so they can be rebuilt at any time.

Costs are estimates from the token counts in each record. Haiku 5.5's cache prices are assumed (see `analysis/bargain.py`).

## Runs

| Run | Date (UTC) | Game | Model | Games | Sandbox | Cost |
|---|---|---|---|---|---|---|
| [`smoke`](smoke) | 2026-10-13 | Battleship, socket, condition A | Opus 5.5 | 1 | local process | $0.77 |
| [`smoke-file`](smoke-file) | 2026-10-13 | Battleship, file, condition A | Opus 5.5 | 1 | Docker | $1.55 |
| [`pilot-1`](pilot-1) | 2026-10-13 | Battleship, all six cells, 3 games each | Opus 5.5 | 17 finished, 5 cut off | Docker | $27.03 |
| [`bargain-smoke`](bargain-smoke) | 2026-10-13 | Negotiation, single pot | Haiku 5.5 | 1 | Docker | $0.01 |
| [`bargain-pilot-haiku`](bargain-pilot-haiku) | 2026-10-13 | Negotiation, single pot | Haiku 5.5 | 20 | Docker | $0.14 |
| [`bargain-pilot-opus`](bargain-pilot-opus) | 2026-10-13 | Negotiation, single pot (games 0 to 4, paired with Haiku) | Opus 5.5 | 5 | Docker | $1.00 |
| [`session-smoke`](session-smoke) | 2026-10-13 | Negotiation, 10-pot session with drafts | Haiku 5.5 | 1 | Docker | $0.09 |
| [`sessions-haiku`](sessions-haiku) | 2026-10-13 | Negotiation, 10-pot sessions with drafts | Haiku 5.5 | 10 | Docker | $0.95 |
| [`episodes`](episodes) | | Self-modelling and renegotiation measured across `smoke`, `smoke-file` and `pilot-1` | | | | |
| [`bargain-pilot-summary`](bargain-pilot-summary) | | `bargain-pilot-haiku` and `bargain-pilot-opus` analysed together | | | | |

`pilot-1` ran out of API credit twice. Games cut off by that are kept as `aborted_game_NNN_k.jsonl` and were replayed with `--resume`. One cut-off game, `socket-C/aborted_game_000_1`, holds the condition C episode where an agent misreported a hit, was challenged by its opponent, and corrected itself with an apology and a compensation shot. The last game, `socket-A/game_002`, was cut off by the second credit run-out and never replayed; its record ends with `end_reason: "error"`.

What the runs found is written up in [docs/findings.md](../docs/findings.md).

## Files

In each run folder:

| File | What it is |
|---|---|
| `manifest.json` | Run settings: config snapshot, git commit, Docker image, start time. A resumed run adds `manifest_2.json`. |
| `<cell>/game_NNN.jsonl` | The full record of one game: every message on the wire, every tool call, every model response with reasoning summaries and token usage, and the ground truth. |
| `<cell>/game_NNN.md` | The same game as a readable transcript (renders on GitHub). Battleship transcripts mark each result call true or false and each shot with what it really hit. |
| `<cell>/game_NNN.html` | Negotiation sessions only: a formatted page with folding pots, drafts against finals, and secret ballots. Open it locally in a browser. |
| `metrics_games.csv`, `metrics_cells.csv`, `review.jsonl` | Battleship analysis (`python -m analysis.metrics`). |
| `bargain_sessions.csv`, `bargain_pots.csv`, `bargain_review.jsonl` | Negotiation analysis (`python -m analysis.bargain`). |
| `review.jsonl` / `bargain_review.jsonl` | Every heuristic hit, for a person to check. |

## Rebuilding the analysis

```bash
python -m analysis.metrics records/pilot-1
python -m analysis.episodes records/pilot-1 records/smoke records/smoke-file --out records/episodes
python -m analysis.bargain records/sessions-haiku
python -m analysis.bargain records/bargain-pilot-haiku records/bargain-pilot-opus --out records/bargain-pilot-summary
```
