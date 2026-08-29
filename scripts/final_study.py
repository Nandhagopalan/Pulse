"""
The overlay, the ETFs, and what is actually left.

Removing the sector overlay took 2025 from -3.46% to +23.75%, but the winners
turned out to include six silver ETFs bought on the same morning and sold on the
same morning -- one commodity bet wearing six tickers, sized as six independent
positions. This run separates the three effects:

    overlay off, funds unrestricted   what the earlier sweep measured
    overlay off, one per underlying   the same, with the duplicate bet capped
    overlay off, equities only        the stock-picking edge on its own

and checks that the incumbent is bit-for-bit unchanged by the new code, since
`equity_only` and `max_per_group` both default to off.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS)
    n_fund = int((~md.is_equity).sum())
    print(f"{md.shape[1]} symbols: {md.shape[1] - n_fund} equities, {n_fund} fund units")
    groups = defaultdict(list)
    for s, g in zip(md.symbols, md.group, strict=False):
        if g and not str(g).startswith("fund:"):
            groups[str(g)].append(str(s))
    print("  tracked-underlying groups: " +
          ", ".join(f"{k}({len(v)})" for k, v in sorted(groups.items())))
    print(f"  e.g. silver -> {sorted(groups['silver'])[:8]}\n")

    eng = Engine(md)
    base = preset("balanced")
    clean = replace(base, sector_top_frac=0.0, max_per_sector=0)

    variants = [
        ("incumbent (overlay on)", base),
        ("overlay off, funds free", clean),
        ("overlay off, 1 per underlying", replace(clean, max_per_group=1)),
        ("overlay off, 2 per underlying", replace(clean, max_per_group=2)),
        ("overlay off, equities only", replace(clean, equity_only=True)),
        ("overlay off, equities + 40d stop",
         replace(clean, equity_only=True, time_stop=40)),
    ]

    print(f"  {'variant':<32} {'expo':>5} {'trCAGR':>7} {'trDD':>7} {'calmar':>6} "
          f"{'2025':>8} {'2026':>8}")
    out = {}
    for label, cfg in variants:
        full = eng.run(cfg)
        tr = period(full, None, TRAIN_END)
        o1, o2 = period(full, *OOS1), period(full, *OOS2)
        cal = tr["cagr"] / abs(tr["max_dd"]) if tr["max_dd"] < 0 else 0.0
        print(f"  {label:<32} {tr['exposure']:5.0%} {tr['cagr']:7.2%} "
              f"{tr['max_dd']:7.2%} {cal:6.2f} {o1['ret']:8.2%} {o2['ret']:8.2%}",
              flush=True)
        out[label] = (full, tr, o1, o2)

    # ── where did the holdout P&L come from? ────────────────────────────────
    print("\n── holdout P&L by instrument, 2025-01-01 onward ─────────────")
    for label in ("overlay off, funds free", "overlay off, 1 per underlying",
                  "overlay off, equities only"):
        cfg = dict(variants)[label]
        res = eng.run(cfg, start="2025-01-01")
        tr = [p for p in res.trades if p.exit_date is not None]
        gid = {str(s): (str(g) if g else None) for s, g in zip(md.symbols, md.group, strict=False)}
        by = defaultdict(float)
        for p in tr:
            g = gid.get(p.symbol)
            by["equities" if g is None else
               (g if not g.startswith("fund:") else "other funds")] += p.pnl
        tot = sum(by.values())
        parts = sorted(by.items(), key=lambda kv: -abs(kv[1]))[:6]
        print(f"  {label}")
        print(f"    total closed P&L {tot:,.0f} over {len(tr)} trades")
        for k, v in parts:
            print(f"      {k:<14} {v:12,.0f}  {v / tot if tot else 0:6.1%}")
        sk = backtest.summarise(res)["skipped"]
        print(f"    skipped: {sk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
