"""
Is the no-sector-overlay result tradeable, or an artifact?

Dropping the sector overlay turns 2025 from -3.46% into +23.75%. A number that
large arriving from *removing* a filter deserves suspicion before it deserves
capital, so this checks the four ways it could be false:

  1. liquidity   -- the book buys names too thin to actually fill
  2. concentration -- two lucky holdings carry the whole year
  3. fragility   -- the edge evaporates under worse fills
  4. universe    -- the overlay was silently a large-cap restriction all along

Only the fourth is a reason to change anything. The first three would each mean
the number is real in the backtest and unavailable in the market.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")


def trades_in(res, start, end):
    a, b = np.datetime64(start), np.datetime64(end) if end else res.dates[-1]
    return [p for p in res.trades if p.exit_date is not None and a <= p.exit_date <= b]


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS)
    eng = Engine(md)
    base = preset("balanced")
    nosec = replace(base, sector_top_frac=0.0)
    labelled = {str(s) for s, lab in zip(md.symbols, md.sector, strict=False) if lab} if md.sector is not None else set()

    # ── 4. what the overlay was actually filtering ──────────────────────────
    print("── universe reach ──────────────────────────────────────────")
    print(f"  symbols in lake                {len(md.symbols)}")
    print(f"  symbols with a sector label    {len(labelled)}  "
          f"(today's NIFTY 500 -- the only sector map available)")
    print("  the overlay therefore confines the book to present-day NIFTY 500\n")

    for label, cfg in (("incumbent", base), ("no sector overlay", nosec)):
        res = eng.run(cfg)
        print(f"── {label} ─────────────────────────────────────────")
        for tag, (s0, s1) in (("2025", OOS1), ("2026 YTD", OOS2)):
            tr = trades_in(res, s0, s1)
            if not tr:
                continue
            pnl = np.array([p.pnl for p in tr])
            rets = np.array([p.ret for p in tr])
            order = np.argsort(-pnl)
            tot = pnl.sum()
            top5 = pnl[order[:5]].sum()
            inuni = sum(1 for p in tr if p.symbol in labelled)
            # Position size against the liquidity that was available to fill it.
            ratios = []
            for p in tr:
                ti = int(np.searchsorted(md.dates, p.entry_date))
                tv = float(np.nanmedian(md.turnover[max(0, ti - 20):ti,
                                                    p.col])) if ti > 0 else np.nan
                if np.isfinite(tv) and tv > 0:
                    ratios.append(p.qty * p.entry / tv)
            ratios = np.array(ratios) if ratios else np.array([np.nan])
            per = period(res, s0, s1)
            print(f"  {tag}: ret {per['ret']:7.2%}  {len(tr)} closed  "
                  f"win {np.mean(rets > 0):5.1%}  mean {np.mean(rets):+6.2%}")
            print(f"       top-5 trades = {top5 / tot if tot else 0:5.1%} of gross P&L; "
                  f"best {rets.max():+.1%} ({tr[order[0]].symbol})")
            print(f"       in today's NIFTY 500: {inuni}/{len(tr)}")
            print(f"       position as x of 20d median turnover: "
                  f"median {np.nanmedian(ratios):.2f}x  p90 {np.nanpercentile(ratios, 90):.2f}x  "
                  f"max {np.nanmax(ratios):.2f}x")
            top = Counter(p.symbol for p in tr).most_common(5)
            print(f"       most-traded names: {', '.join(f'{s}x{n}' for s, n in top)}")
        s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
        print(f"  train skipped-for-size: {s['skipped'].get('size', 0)}\n")

    # ── 3. fragility to fills ───────────────────────────────────────────────
    # A book that has moved down the liquidity curve should be hurt more by
    # worse fills than one trading the largest 500 names. If the no-sector edge
    # survives triple slippage it is not a microstructure illusion.
    print("── slippage stress (per side) ──────────────────────────────")
    print(f"  {'variant':<22} {'slip':>6} {'trCAGR':>8} {'2025':>8} {'2026':>8}")
    for label, cfg in (("incumbent", base), ("no sector overlay", nosec)):
        for slip in (0.0020, 0.0035, 0.0060):
            c = replace(cfg, slippage=slip)
            full = eng.run(c)
            tr = period(full, None, TRAIN_END)
            print(f"  {label:<22} {slip:6.2%} {tr['cagr']:8.2%} "
                  f"{period(full, *OOS1)['ret']:8.2%} {period(full, *OOS2)['ret']:8.2%}")

    # ── 2. is the whole-history record any good without the overlay? ────────
    print("\n── year by year, no sector overlay vs incumbent ────────────")
    a = backtest.by_year(eng.run(base))
    b = backtest.by_year(eng.run(nosec))
    print(f"  {'year':<6} {'incumbent':>10} {'no-overlay':>11}")
    for (y, ra, _, _), (_, rb, _, _) in zip(a, b, strict=False):
        flag = "  <- holdout" if y >= 2025 else ""
        print(f"  {y:<6} {ra:10.2%} {rb:11.2%}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
