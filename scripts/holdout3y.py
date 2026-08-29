"""
A three-year holdout: train to 2023, then face 2024, 2025 and 2026 cold.

The earlier split trained through 2024 and held out twenty months. This pulls
2024 into the reserve as well, which makes the test materially harder — 2024
carries the worst drawdown in the whole record, and it is now a year the search
has never seen. Parameters are re-derived from scratch on 2008-2023; nothing
selected on the old split is carried over, because a config chosen while 2024
was visible cannot be scored on 2024.

Both books are re-selected under their own drawdown budget, so the question
"does the conservative one still hold -10%" is answered on data it never saw.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

TRAIN_END = "2024-01-01"          # last training session is 2023-12-31
Y2024 = ("2024-01-01", "2025-01-01")
Y2025 = ("2025-01-01", "2026-01-01")
Y2026 = ("2026-01-01", None)
MIN_TRADES = 250                  # a shorter training window, so a lower floor

HEAD = (f"  {'variant':<34} {'CAGR':>7} {'maxDD':>7} {'calmar':>6} {'expo':>6} "
        f"{'trades':>6} {'expR':>6}")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    core = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                   max_per_group=1, require_sector_label=False)
    shapes = [
        ("60d time stop", core),
        ("weekly EMA20 trail, no time stop",
         replace(core, weekly_ema_exit=20, time_stop=10**6)),
    ]

    print("── selection on TRAIN 2008-01 .. 2023-12 only ──────────────")
    print(HEAD)
    scored: List[Tuple[str, StrategyConfig, dict]] = []
    for shape, cfg0 in shapes:
        for rp in (0.0030, 0.0040, 0.0050, 0.0060, 0.0080):
            cfg = replace(cfg0, risk_pct=rp)
            s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
            cal = s["cagr"] / abs(s["max_dd"]) if s["max_dd"] < 0 else 0
            print(f"  {f'{shape[:22]}, r={rp:.2%}':<34} {s['cagr']:7.2%} "
                  f"{s['max_dd']:7.2%} {cal:6.2f} {s['exposure']:6.1%} "
                  f"{s['n_trades']:6d} {s['expectancy_r']:+6.2f}", flush=True)
            if s["n_trades"] >= MIN_TRADES:
                scored.append((f"{shape}, risk {rp:.2%}", cfg, s))

    picks = []
    for budget, name in ((-0.10, "conservative"), (-0.15, "stretched")):
        fits = [x for x in scored if x[2]["max_dd"] >= budget]
        if fits:
            picks.append((name, budget, max(fits, key=lambda x: x[2]["cagr"])))

    print("\n── frozen picks, chosen without ever seeing 2024-2026 ──────")
    for name, budget, (label, _cfg, s) in picks:
        print(f"  {name:<14} budget {budget:.0%}  ->  {label}")
        print(f"                 TRAIN CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  "
              f"win {s['win_rate']:.1%}  expR {s['expectancy_r']:+.2f}")

    print("\n── the three held-out years ────────────────────────────────")
    print(f"  {'book':<14} {'2024':>9} {'2024 DD':>9} {'2025':>9} {'2025 DD':>9} "
          f"{'2026':>9} {'2026 DD':>9} {'3yr CAGR':>9}")
    for name, _budget, (_label, cfg, _s) in picks:
        full = eng.run(cfg)
        cells = []
        for win in (Y2024, Y2025, Y2026):
            p = period(full, *win)
            cells.append((p["ret"], p["max_dd"]))
        hold = period(full, "2024-01-01", None)
        print(f"  {name:<14} " + " ".join(f"{r:9.2%} {d:9.2%}" for r, d in cells)
              + f" {hold['cagr']:9.2%}")

    print("\n── full record on each frozen pick ─────────────────────────")
    for name, _budget, (label, cfg, _s) in picks:
        full = eng.run(cfg)
        fs = backtest.summarise(full)
        hold = period(full, "2024-01-01", None)
        print(f"\n  {name}: {label}")
        print("   ", {k: round(float(fs[k]), 4) for k in
              ("cagr", "max_dd", "sharpe", "win_rate", "payoff", "profit_factor",
               "expectancy_r", "median_hold", "exposure")})
        print(f"    trades {fs['n_trades']}   end Rs {fs['end']:,.0f}")
        print(f"    held-out 3 years: {hold['ret']:.2%} total, "
              f"{hold['cagr']:.2%} annualised, worst DD {hold['max_dd']:.2%}")
        for y, r, _e, dd in backtest.by_year(full):
            flag = "  <- held out" if y >= 2024 else ""
            print(f"      {y}  {r:8.2%}  worst {dd:7.2%}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
