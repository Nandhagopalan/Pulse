"""
How much bigger is the real drawdown than the one the backtest promised?

Both books sized on 2008-2023 breached their budget in 2024, the first year they
had not seen: -9.32% became -14.14%, -11.57% became -16.61%. That is not a bad
selection, it is what fitting a drawdown does. Sizing is tuned against the worst
episode the training data happens to contain, and the next unseen year is free
to be worse.

So rather than pick one number, this walks the whole sizing ladder and reports
the ratio between the drawdown promised on training data and the drawdown
actually taken across the three held-out years. If the ratio is stable, it can be
budgeted for: divide the target by it and size to that instead.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")
TRAIN_END = "2024-01-01"


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    shape = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                    max_per_group=1, require_sector_label=False,
                    weekly_ema_exit=20, time_stop=10**6)

    print("  Train = 2008-01..2023-12.  Held out = 2024, 2025, 2026 YTD.\n")
    print(f"  {'risk':>6} {'trCAGR':>8} {'trDD':>8} | {'3yr CAGR':>9} {'3yr DD':>8} "
          f"{'DD ratio':>9} | {'2024':>8} {'2025':>8} {'2026':>8}")
    for rp in (0.0020, 0.0025, 0.0030, 0.0035, 0.0040, 0.0050, 0.0060):
        cfg = replace(shape, risk_pct=rp)
        tr = backtest.summarise(eng.run(cfg, end=TRAIN_END))
        full = eng.run(cfg)
        hold = period(full, "2024-01-01", None)
        yr = [period(full, a, b)["ret"] for a, b in
              (("2024-01-01", "2025-01-01"), ("2025-01-01", "2026-01-01"),
               ("2026-01-01", None))]
        ratio = hold["max_dd"] / tr["max_dd"] if tr["max_dd"] < 0 else 0
        print(f"  {rp:6.2%} {tr['cagr']:8.2%} {tr['max_dd']:8.2%} | "
              f"{hold['cagr']:9.2%} {hold['max_dd']:8.2%} {ratio:9.2f}x | "
              + " ".join(f"{v:8.2%}" for v in yr), flush=True)

    print("\n  A ratio consistently above 1 means the backtest drawdown is an")
    print("  underestimate, and the budget has to be set below the target to hit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
