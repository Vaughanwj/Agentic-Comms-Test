"""A formatted, self-contained HTML view of one negotiation record.

    python -m analysis.bargain --transcript --html records/<run_id>/<cell>/game_000.jsonl

Pots fold open and shut; each shows the private fallbacks, every message (public
or private), the draft, the final proposal with changes from the draft, the
secret ballots, and running totals.
"""
from __future__ import annotations

from html import escape

PALETTE = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed", "#0891b2", "#dc2626"]

CSS = """
:root { --bg:#f7f7f5; --card:#ffffff; --text:#1c1c1a; --muted:#6b6b66; --line:#e3e2dd;
        --yes:#15803d; --no:#b91c1c; --draft:#fff7e6; --final:#eef4ff; --private:#fdf2f8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151514; --card:#1f1f1d; --text:#ecebe6; --muted:#a3a29b; --line:#34332f;
          --yes:#4ade80; --no:#f87171; --draft:#2a2416; --final:#18223a; --private:#2b1a24; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width: 880px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
.meta { color: var(--muted); margin: 0 0 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin: 10px 0; }
.who { font-weight: 600; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:baseline; }
.to { color: var(--muted); font-size: .85rem; margin-left: 6px; }
.private { background: var(--private); }
.private .to { font-weight: 600; }
.body { white-space: pre-wrap; margin-top: 6px; }
details.pot { background: var(--card); border:1px solid var(--line); border-radius: 12px; margin: 14px 0; }
details.pot > summary { cursor:pointer; padding: 14px 16px; list-style:none; display:flex; flex-wrap:wrap; gap:8px 14px;
                        align-items:center; }
details.pot > summary::-webkit-details-marker { display:none; }
details.pot > summary::before { content:"▸"; color: var(--muted); }
details.pot[open] > summary::before { content:"▾"; }
details.pot > .inner { padding: 0 16px 12px; }
.pot-title { font-weight: 700; }
.pill { font-size:.8rem; border:1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); }
.pill.pass { color: var(--yes); border-color: currentColor; }
.pill.fail { color: var(--no); border-color: currentColor; }
.round { margin: 18px 0 6px; font-weight: 600; color: var(--muted); text-transform: uppercase; font-size: .8rem;
         letter-spacing: .04em; }
.split { display:flex; height: 22px; border-radius: 6px; overflow:hidden; margin: 8px 0 6px; border:1px solid var(--line); }
.split span { display:flex; align-items:center; justify-content:center; color:#fff; font-size:.75rem; font-weight:600;
              min-width: 0; overflow:hidden; white-space:nowrap; }
.shares { display:flex; flex-wrap:wrap; gap: 4px 14px; font-size: .9rem; }
.delta-up { color: var(--yes); } .delta-down { color: var(--no); }
.draft { background: var(--draft); } .final { background: var(--final); }
.label { font-weight: 700; }
.ballots { display:flex; flex-wrap:wrap; gap: 6px; margin-top: 8px; }
.ballot { font-size:.85rem; border-radius: 6px; padding: 2px 8px; border:1px solid currentColor; }
.ballot.yes { color: var(--yes); } .ballot.no { color: var(--no); }
.fallbacks { color: var(--muted); font-size: .9rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
td, th { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; }
.bar { height: 10px; border-radius: 4px; }
.controls { margin: 8px 0 0; }
button { font: inherit; font-size: .85rem; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--line);
         background: var(--card); color: var(--text); cursor: pointer; }
"""


def render_html(records: list[dict]) -> str:
    start = next(r for r in records if r["kind"] == "game_start")
    end = next((r for r in records if r["kind"] == "game_end"), {})
    cfg = start["config"]
    names = list(start["fallbacks"])
    colour = {p: PALETTE[i % len(PALETTE)] for i, p in enumerate(names)}
    model = start["models"][names[0]]
    pots = cfg.get("pots", 1) if start.get("harness_version") != "bargain-1" else 1

    def who(p: str) -> str:
        return f'<span class="dot" style="background:{colour[p]}"></span><span class="who">{escape(p)}</span>'

    def split_bar(shares: dict) -> str:
        total = sum(shares.values()) or 1
        parts = "".join(
            f'<span style="width:{100 * v / total:.2f}%;background:{colour[p]}" title="{escape(p)} {v}">'
            f'{v if v * 100 / total >= 6 else ""}</span>' for p, v in shares.items() if v > 0)
        return f'<div class="split">{parts}</div>'

    def share_list(shares: dict, before: dict | None = None) -> str:
        items = []
        for p, v in shares.items():
            delta = ""
            if before is not None and before.get(p) != v:
                d = v - before[p]
                delta = f' <span class="delta-{"up" if d > 0 else "down"}">({d:+d})</span>'
            items.append(f"<span>{who(p)} {v}{delta}</span>")
        return f'<div class="shares">{"".join(items)}</div>'

    # Summaries for each pot's fold-out heading.
    pot_summary: dict[int, str] = {}
    pot_index = 0
    for r in records:
        if r["kind"] == "pot_start":
            pot_index = r["pot"]
        elif r["kind"] == "round_result" and r["result"]["passed"]:
            res = r["result"]
            ordered = sorted(res["proposal"]["shares"].items(), key=lambda kv: -kv[1])
            pot_summary[pot_index or 1] = (
                f'<span class="pill pass">passed round {res["round"]}</span>'
                f'<span>proposer {who(res["proposal"]["proposer"])}</span>'
                f'<span class="fallbacks">{" · ".join(f"{escape(p)} {v}" for p, v in ordered)}</span>')

    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
           f"<title>Negotiation {start['game_index']}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Negotiation, game {start['game_index']}</h1>",
           f"<p class='meta'>{escape(model)} · {len(names)} agents · {pots} pot{'s' if pots > 1 else ''} · "
           f"messaging {escape(cfg['messaging'])} · votes {'by name' if cfg.get('reveal_votes') else 'secret'}"
           f"{' · drafts' if cfg.get('draft') else ''}<br>Proposer order"
           f"{' for pot 1' if pots > 1 else ''}: {', '.join(escape(p) for p in start['proposer_order'])}</p>"]

    totals = end.get("payoffs") or {}
    if totals:
        top = max(totals.values()) or 1
        rows = "".join(
            f"<tr><td>{who(p)}</td><td>{v}</td><td style='width:60%'><div class='bar' "
            f"style='width:{100 * v / top:.1f}%;background:{colour[p]}'></div></td></tr>"
            for p, v in sorted(totals.items(), key=lambda kv: -kv[1]))
        out.append(f"<div class='card'><div class='label'>{'Final totals' if pots > 1 else 'Payoffs'}</div>"
                   f"<table>{rows}</table></div>")
    if pots > 1:
        out.append("<div class='controls'><button onclick=\"document.querySelectorAll('details.pot')"
                   ".forEach(d=>d.open=true)\">Open all pots</button> <button onclick=\"document.querySelectorAll"
                   "('details.pot').forEach(d=>d.open=false)\">Close all</button></div>")

    open_pot = False
    draft_shares: dict | None = None

    def open_pot_block(n: int, size: int, fallbacks: dict) -> str:
        fb = " · ".join(f"{escape(p)} {v}" for p, v in fallbacks.items())
        summary = pot_summary.get(n, "<span class='pill fail'>no deal</span>")
        return (f"<details class='pot'{' open' if n == 1 else ''}><summary><span class='pot-title'>Pot {n}</span>"
                f"<span class='pill'>{size} points</span>{summary}"
                f"</summary><div class='inner'><p class='fallbacks'>Private fallbacks: {fb}</p>"
                f"<div class='round'>Round 1</div>")

    if pots == 1:
        out.append(open_pot_block(1, cfg["pot"], start["fallbacks"]))
        open_pot = True
    for r in records:
        kind = r["kind"]
        if kind == "pot_start":
            if open_pot:
                out.append("</div></details>")
            out.append(open_pot_block(r["pot"], r["size"], r["fallbacks"]))
            open_pot = True
        elif kind == "tap" and r.get("event") == "send":
            private = r["recipient"] != "all"
            to = f"private → {escape(r['recipient'])}" if private else "to everyone"
            out.append(f"<div class='card{' private' if private else ''}'>{who(r['sender'])}"
                       f"<span class='to'>{to} · #{r['msg_seq']}</span><div class='body'>{escape(r['body'].strip())}"
                       f"</div></div>")
        elif kind == "draft":
            draft_shares = r["shares"]
            out.append(f"<div class='card draft'><span class='label'>Draft</span> by {who(r['player'])}"
                       f"{split_bar(r['shares'])}{share_list(r['shares'])}</div>")
        elif kind == "proposal":
            changed = draft_shares is not None and draft_shares != r["shares"]
            note = " (changed from the draft)" if changed else (" (same as the draft)" if draft_shares else "")
            out.append(f"<div class='card final'><span class='label'>Final proposal</span> by {who(r['player'])}"
                       f"{escape(note)}{split_bar(r['shares'])}"
                       f"{share_list(r['shares'], draft_shares if changed else None)}</div>")
        elif kind == "round_result":
            res = r["result"]
            draft_shares = None
            if res["proposal"] is None:
                verdict = "<span class='pill fail'>no proposal</span>"
            else:
                verdict = (f"<span class='pill {'pass' if res['passed'] else 'fail'}'>"
                           f"{'passed' if res['passed'] else 'failed'} {res['yes']}–{res['no']}</span>")
            ballots = "".join(f"<span class='ballot {v}'>{escape(p)}: {v}</span>" for p, v in res["ballots"].items())
            out.append(f"<div class='card'><span class='label'>Vote</span> {verdict}"
                       f"<div class='ballots'>{ballots}</div>"
                       f"<p class='fallbacks' style='margin:8px 0 0'>Ballots are secret: agents saw only the tally."
                       f"</p></div>")
            if not res["passed"] and res["round"] < cfg["max_rounds"]:
                out.append(f"<div class='round'>Round {res['round'] + 1} · pot shrinks</div>")
        elif kind == "pot_end" and pots > 1:
            tot = " · ".join(f"{escape(p)} {v}" for p, v in r["totals"].items())
            out.append(f"<p class='fallbacks'>Totals after pot {r['pot']}: {tot}</p>")
    if open_pot:
        out.append("</div></details>")
    out.append("</main></body></html>")
    return "".join(out)
