"""
Are the big holdout winners real, or corporate-action artifacts?

A momentum book without the sector overlay buys smaller names, and a missed or
mis-scaled split on a thin stock manufactures a gain the backtest books happily.
This re-prices the largest winners from the *unadjusted* bhavcopy: if the split-
adjusted return and the raw return disagree, an action fell inside the holding
window and the trade needs a human look. If they agree, the move is what the
tape actually did.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import OOS1, Engine

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDEX = os.environ.get("PULSE_INDEX")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS)
    col_of = {str(s): i for i, s in enumerate(md.symbols)}

    if INDEX:
        t = pq.read_table(INDEX)
        nm = t.column("index_name").to_pylist()
        dt = t.column("date").to_pylist()
        cl = t.column("close").to_pylist()
        print("── small/mid benchmarks over the holdout ───────────────────")
        for want in ("NIFTY SMLCAP 100", "NIFTY SMALLCAP 100", "NIFTY MIDCAP 100",
                     "NIFTY MICROCAP 250", "NIFTY SMLCAP 250"):
            idx = [i for i, n in enumerate(nm) if n == want]
            if not idx:
                continue
            d = np.array([dt[i] for i in idx], dtype="datetime64[D]")
            c = np.array([cl[i] for i in idx], float)
            o = np.argsort(d)
            d, c = d[o], c[o]
            for tag, (s0, s1) in (("2025", OOS1), ("2026 YTD", ("2026-01-01", None))):
                lo = int(np.searchsorted(d, np.datetime64(s0)))
                hi = int(np.searchsorted(d, np.datetime64(s1))) if s1 else len(d)
                if hi - lo > 2:
                    print(f"  {want:<20} {tag:<9} {c[hi-1]/c[lo]-1:+7.2%}")
        print()

    eng = Engine(md)
    cfg = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0)
    res = eng.run(cfg, start="2025-01-01")
    tr = [p for p in res.trades if p.exit_date is not None]
    tr.sort(key=lambda p: -p.pnl)

    print("── largest winners: split-adjusted vs raw tape ─────────────")
    print(f"  {'symbol':<12} {'entry':<11} {'exit':<11} {'adj ret':>8} "
          f"{'raw ret':>8} {'gap':>7}  {'P&L':>12}")
    flagged = []
    for p in tr[:15]:
        j = col_of[p.symbol]
        i0 = int(np.searchsorted(md.dates, p.entry_date))
        i1 = int(np.searchsorted(md.dates, p.exit_date))
        raw0, raw1 = md.raw_close[i0, j], md.raw_close[i1, j]
        raw = float(raw1 / raw0 - 1.0) if np.isfinite(raw0) and raw0 > 0 else np.nan
        gap = p.ret - raw
        mark = "  <-- CHECK" if abs(gap) > 0.05 else ""
        if mark:
            flagged.append(p.symbol)
        print(f"  {p.symbol:<12} {p.entry_date!s:<11} {p.exit_date!s:<11} "
              f"{p.ret:8.2%} {raw:8.2%} {gap:7.2%}  {p.pnl:12,.0f}{mark}")

    print(f"\n  trades whose adjusted and raw returns disagree by >5pp: "
          f"{len(flagged)} of 15  {flagged if flagged else ''}")

    tot = sum(p.pnl for p in tr)
    print(f"\n  total closed P&L {tot:,.0f} over {len(tr)} trades; "
          f"top 15 = {sum(p.pnl for p in tr[:15]) / tot:.1%}")
    losers = [p for p in tr if p.pnl <= 0]
    print(f"  {len(losers)} losers, worst {min(p.ret for p in tr):.1%}; "
          f"win rate {1 - len(losers)/len(tr):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
