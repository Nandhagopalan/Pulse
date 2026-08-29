"""
The best book that still fits inside a -15% drawdown, on the stated terms.

The wide matrix showed only one of 37 variants inside the cap, and it was the
low-deployment baseline — the search had climbed toward return and left the
budget behind. This narrows it: take the two exits that actually earned their
place (a weekly EMA trail, and no fixed time stop), then walk deployment up from
the bottom until the drawdown budget binds.

Run twice, with idle cash earning nothing and earning 5%, because that single
assumption is worth more than every rule change tested.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")
DD_CAP = -0.15

HEAD = (f"  {'variant':<40} {'CAGR':>7} {'maxDD':>7} {'calmar':>6} {'expo':>6} "
        f"{'trades':>6} {'expR':>6}")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    v2 = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                 max_per_group=1, require_sector_label=False)
    # The two exits the matrix supported: trail on a weekly close, and stop
    # cutting winners at 60 sessions.
    best_exit = replace(v2, weekly_ema_exit=20, time_stop=10**6)

    keep = []
    for cy, tag in ((0.0, "0% on cash"), (0.05, "5% on cash")):
        print(f"\n── deployment ladder, {tag} ─────────────────────────")
        print(HEAD)
        for rp in (0.004, 0.006, 0.008, 0.010, 0.013, 0.016, 0.020):
            cfg = replace(best_exit, cash_yield=cy, risk_pct=rp)
            s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
            cal = s["cagr"] / abs(s["max_dd"]) if s["max_dd"] < 0 else 0
            ok = s["max_dd"] >= DD_CAP and s["n_trades"] >= 300
            print(f"  {f'risk {rp:.1%}, {tag}':<40} {s['cagr']:7.2%} {s['max_dd']:7.2%} "
                  f"{cal:6.2f} {s['exposure']:6.1%} {s['n_trades']:6d} "
                  f"{s['expectancy_r']:+6.2f}" + ("  <- fits" if ok else ""), flush=True)
            if ok:
                keep.append((f"risk {rp:.1%}, {tag}", cfg, s))

    if not keep:
        print("\nnothing fits")
        return 1
    label, chosen, _tr = max(keep, key=lambda x: x[2]["cagr"])
    print(f"\n── best inside the budget: {label} ──────────────────")
    full = eng.run(chosen)
    print(f"  {'period':<14} {'return':>9} {'maxDD':>8} {'expo':>7}")
    for tag, (s0, s1) in (("TRAIN", (None, TRAIN_END)), ("2025", OOS1), ("2026 YTD", OOS2)):
        p = period(full, s0, s1)
        print(f"  {tag:<14} {p['ret']:9.2%} {p['max_dd']:8.2%} {p['exposure']:7.1%}")
    s = backtest.summarise(full)
    print("\n  full period:", {k: round(float(s[k]), 4) for k in
          ("cagr", "max_dd", "sharpe", "win_rate", "payoff", "profit_factor",
           "expectancy_r", "median_hold", "exposure")})
    print(f"  trades {s['n_trades']}   end Rs {s['end']:,.0f}")
    print("\n  year by year")
    for y, r, _e, dd in backtest.by_year(full):
        print(f"    {y}  {r:8.2%}  worst {dd:7.2%}" + ("  <- held out" if y >= 2025 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
