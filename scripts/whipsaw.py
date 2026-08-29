"""
Does damping the regime's flipping help, and does it survive the holdout?

2022 and 2024 were not weak because the signals were poor — 2024 had the best
win rate in the sample. They shared a different property with 2015 and 2025: the
regime switch flipped nine to eighteen times, turning the whole book over on
each crossing. That is a recurring mechanism rather than a two-year accident,
which makes it worth attacking; "make 2022 and 2024 bigger" would not be.

Two asymmetric dampers, both leaving the exit as immediate as it is now, because
delaying the exit was already measured to deepen drawdowns badly:

    regime_entry_confirm   wait N sessions after the switch turns ON
    regime_band            require the index to clear its average by a margin

Selected on TRAIN, then applied to 2025 and 2026 untouched.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

HEAD = (f"  {'variant':<34} {'CAGR':>7} {'maxDD':>7} {'calmar':>6} {'2022':>7} "
        f"{'2024':>7} {'flips':>6} {'trades':>6}")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    yrs = md.dates.astype("datetime64[Y]").astype(int) + 1970
    v3 = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                 max_per_group=1, require_sector_label=False, risk_pct=0.0040,
                 weekly_ema_exit=20, time_stop=10**6)

    def show(label, cfg, keep):
        s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
        full = eng.run(cfg)
        by = {y: r for y, r, _e, _d in backtest.by_year(full)}
        reg = eng.feats(cfg).regime
        tm = (yrs >= 2008) & (yrs <= 2024)
        flips = int(np.count_nonzero(np.diff(reg[tm].astype(int)) != 0))
        cal = s["cagr"] / abs(s["max_dd"]) if s["max_dd"] < 0 else 0
        print(f"  {label:<34} {s['cagr']:7.2%} {s['max_dd']:7.2%} {cal:6.2f} "
              f"{by.get(2022, 0):7.2%} {by.get(2024, 0):7.2%} {flips:6d} "
              f"{s['n_trades']:6d}", flush=True)
        keep.append((label, cfg, s, full))

    rows: list = []
    print("── damping the switch, TRAIN 2008-2024 ─────────────────────")
    print(HEAD)
    show("v3 baseline", v3, rows)
    for n in (3, 5, 10, 15, 20):
        show(f"entry confirm {n}d", replace(v3, regime_entry_confirm=n), rows)
    for b in (0.01, 0.02, 0.03, 0.05):
        show(f"regime band {b:.0%}", replace(v3, regime_band=b), rows)
    for b in (0.02, 0.03):
        for n in (5, 10):
            show(f"band {b:.0%} + confirm {n}d",
                 replace(v3, regime_band=b, regime_entry_confirm=n), rows)

    base = rows[0][2]
    better = [r for r in rows[1:]
              if r[2]["cagr"] > base["cagr"] and r[2]["max_dd"] >= base["max_dd"]]
    print(f"\n  {len(better)} variants beat the baseline on BOTH CAGR and drawdown")
    if not better:
        print("  the whipsaw damping does not pay; baseline stands")
        cand = rows[:1]
    else:
        cand = sorted(better, key=lambda r: -r[2]["cagr"])[:3]

    print("\n── held out, never seen during selection ───────────────────")
    print(f"  {'variant':<34} {'2025':>8} {'2026':>8} {'2025 DD':>8} {'2026 DD':>8}")
    for label, _c, _st, full in cand:
        a, b = period(full, *OOS1), period(full, *OOS2)
        print(f"  {label:<34} {a['ret']:8.2%} {b['ret']:8.2%} "
              f"{a['max_dd']:8.2%} {b['max_dd']:8.2%}")

    label, _cfg2, _s2, full = cand[0]
    print(f"\n── full record, {label} ────────────────────────────")
    fs = backtest.summarise(full)
    print("  ", {k: round(float(fs[k]), 4) for k in
          ("cagr", "max_dd", "sharpe", "win_rate", "payoff", "profit_factor",
           "expectancy_r", "median_hold", "exposure")})
    print(f"   trades {fs['n_trades']}   end Rs {fs['end']:,.0f}")
    for y, r, _e, dd in backtest.by_year(full):
        print(f"    {y}  {r:8.2%}  worst {dd:7.2%}" + ("  <- held out" if y >= 2025 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
