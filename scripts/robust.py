"""
Which part of the aggressive frontier is real, and which is selection noise.

Two sweeps produced three configurations, each the highest-CAGR point inside a
drawdown budget on 2008-2023, and their held-out results were 16.84%, 9.66% and
1.96% annualised. Configurations that were near-indistinguishable in training
came apart completely on data they had not seen. Taking the best of them and
reporting it would be reporting the luckiest draw.

So this stops selecting and starts measuring. Every configuration in the grid is
run once over the whole record and split at the same point, which gives a train
score and a holdout score for each. Three things then say whether any of this
generalises:

  * **Rank correlation between train CAGR and holdout CAGR.** Positive means
    training rank carries information. Around zero means the sweep is choosing
    noise, and the argmax is worthless however good it looks.
  * **The distribution of holdout results inside a budget**, not its maximum. A
    band whose median config survives is an edge; one where only the top config
    survives is a fit.
  * **Drawdown inflation per rollback speed** -- train DD against holdout DD.
    A setting that holds its budget out of sample is usable; one that doubles
    is not, whatever it returned.

Nothing here is used to pick. It is used to decide whether picking is justified.

    PULSE_BARS=... uv run python scripts/robust.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.aggressive import stats
from scripts.walkforward import Engine

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

SPLIT = "2024-01-01"


def spearman(a: List[float], b: List[float]) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    base = replace(preset("balanced"),
                   sector_top_frac=0.0, max_per_sector=0, max_per_group=1,
                   require_sector_label=False, time_stop=10 ** 6,
                   cash_yield=0.0, weekly_ema_exit=20)

    rows: List[Tuple[int, StrategyConfig, dict, dict]] = []
    for rma in (50, 75, 100, 150):
        for rp in (0.006, 0.010, 0.020):
            for mp in (12, 15, 20):
                for mw in (0.08, 0.10, 0.125):
                    for sa in (2.0, 2.5, 3.0):
                        cfg = replace(base, regime_ma=rma, risk_pct=rp,
                                      max_positions=mp, max_weight=mw, stop_atr=sa)
                        res = eng.run(cfg)
                        rows.append((rma, cfg, stats(res, None, SPLIT),
                                     stats(res, SPLIT, None)))
        print(f"    [regime_ma {rma}] {len(rows)} run", flush=True)

    tr = [r[2]["cagr"] for r in rows]
    ho = [r[3]["cagr"] for r in rows]
    print(f"\n── does training rank predict holdout rank? ({len(rows)} configs) ──")
    print(f"  Spearman(train CAGR, holdout CAGR) over the whole grid: {spearman(tr, ho):+.3f}")
    for rma in (50, 75, 100, 150):
        g = [r for r in rows if r[0] == rma]
        print(f"    regime_ma {rma:>3}: {spearman([x[2]['cagr'] for x in g], [x[3]['cagr'] for x in g]):+.3f}"
              f"   ({len(g)} configs)")

    print("\n── holdout distribution inside a 18% train-drawdown budget ──")
    print(f"  {'regime_ma':<10} {'n':>4} {'holdout CAGR p25/med/p75':>28} "
          f"{'median DD':>10} {'DD inflation':>13}")
    for rma in (50, 75, 100, 150):
        g = [r for r in rows if r[0] == rma and r[2]["max_dd"] >= -0.18]
        if not g:
            print(f"  {rma:<10} {0:>4}   nothing inside the budget")
            continue
        h = np.array([x[3]["cagr"] for x in g])
        infl = np.array([abs(x[3]["max_dd"]) / max(abs(x[2]["max_dd"]), 1e-9) for x in g])
        q = np.quantile(h, [0.25, 0.5, 0.75])
        print(f"  {rma:<10} {len(g):>4}   {q[0]:>7.2%} {q[1]:>8.2%} {q[2]:>8.2%}   "
              f"{np.median([x[3]['max_dd'] for x in g]):>9.2%} "
              f"{np.median(infl):>12.2f}x")

    print("\n── how often does a budget survive contact with new data? ──")
    for b in (0.15, 0.18, 0.20):
        g = [r for r in rows if r[2]["max_dd"] >= -b]
        if not g:
            continue
        kept = sum(1 for x in g if x[3]["max_dd"] >= -b)
        print(f"  train DD <= {b:.0%}: {len(g):>3} configs, "
              f"{kept:>3} ({kept/len(g):.0%}) also held {b:.0%} out of sample")

    print("\n── the most stable cells: ranked by holdout p25 within budget ──")
    print("  (measurement only -- this is the holdout, so it cannot be selected on)")
    cells: Dict[Tuple[int, float], List[float]] = {}
    for rma, cfg, t, h in rows:
        if t["max_dd"] >= -0.18:
            cells.setdefault((rma, cfg.stop_atr), []).append(h["cagr"])
    for (rma, sa), v in sorted(cells.items(), key=lambda kv: -np.quantile(kv[1], 0.25)):
        print(f"  ma{rma:<4} stop {sa:.1f}   n={len(v):<3} "
              f"p25 {np.quantile(v, 0.25):>7.2%}  median {np.median(v):>7.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
