"""
The deployment ladder, at the one regime speed that has survived every test.

The stability run settled what could and could not be searched here. Across 324
aggressive configurations, the rank correlation between training CAGR and
held-out CAGR was **-0.152** -- training rank does not predict out-of-sample
rank, it mildly inverts it -- and of the nine configurations that met an 18%
training drawdown budget, only three held that budget on data they had not
seen. Searching this space for a best configuration produces a number, not an
edge.

What has survived repeatedly, across independent searches on differently-bounded
windows, is the *shape*: a 100-day regime average, a weekly EMA20 trail, no time
stop. The 50-day regime that looked like the obvious answer to "roll back
faster" trained at -19% and delivered -34%, because a shorter average whipsaws
-- it sells the dip and rebuys higher.

So nothing is searched here. The shape is fixed, and the single remaining
decision -- how hard to deploy when the regime is on -- is presented as a
ladder rather than a pick. A ladder is falsifiable in a way an argmax is not:
if return and drawdown both rise smoothly with size, and the held-out years
track the training years rung for rung, the frontier is real and the choice of
rung is the user's risk appetite. If the rungs jump around, there is nothing
here to stand on and the ladder says so.

    PULSE_BARS=... PULSE_INDEX=... uv run python scripts/ladder.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import List, Tuple

import duckdb
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.aggressive import HOLD, TRAIN_END, monthly, stats
from scripts.benchmarks import CHAINS, curve_stats, load_chain, slice_from
from scripts.walkforward import Engine, period

BARS = os.environ["PULSE_BARS"]
INDEX = os.environ["PULSE_INDEX"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

# n x w = 150%, so the binding constraint is cash, not the per-name cap: the
# book fills every slot it can find and stops when it runs out of money.
RUNGS = [float(x) for x in os.environ["PULSE_RUNGS"].split(",")] \
    if os.environ.get("PULSE_RUNGS") else \
    [0.004, 0.006, 0.008, 0.010, 0.0125, 0.015, 0.020, 0.030]


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    base = replace(preset("balanced"),
                   sector_top_frac=0.0, max_per_sector=0, max_per_group=1,
                   require_sector_label=False, time_stop=10 ** 6,
                   cash_yield=0.0, weekly_ema_exit=20,
                   regime_ma=100, stop_atr=3.0,
                   max_positions=12, max_weight=0.125)

    print("── the ladder: shape fixed, only deployment varies ─────────")
    print("   TRAIN is 2008-2023. 2024-2026 were not seen when the shape was fixed.")
    print(f"\n  {'risk':>7} {'trCAGR':>8} {'trDD':>8} | {'3y CAGR':>8} {'3y DD':>8} "
          f"{'infl':>6} {'recov':>6} | {'2024':>8} {'2025':>8} {'2026':>8} "
          f"| {'expo':>6} {'p95':>6} {'neg':>4}")
    keep: List[Tuple[float, StrategyConfig, backtest.Result]] = []
    for rp in RUNGS:
        cfg = replace(base, risk_pct=rp)
        tr = stats(eng.run(cfg, end=TRAIN_END))
        res = eng.run(cfg)
        keep.append((rp, cfg, res))
        h = stats(res, "2024-01-01", None)
        ys = [period(res, a, b)["ret"] for _l, a, b in HOLD]
        neg = sum(1 for _y, r, _q, _d in backtest.by_year(res) if r < 0)
        infl = abs(h["max_dd"]) / max(abs(tr["max_dd"]), 1e-9)
        print(f"  {rp:>6.2%} {tr['cagr']:>8.2%} {tr['max_dd']:>8.2%} | "
              f"{h['cagr']:>8.2%} {h['max_dd']:>8.2%} {infl:>5.2f}x {h['recover']:>6} | "
              f"{ys[0]:>8.2%} {ys[1]:>8.2%} {ys[2]:>8.2%} | "
              f"{h['expo']:>6.1%} {h['peak_expo']:>6.1%} {neg:>4}")

    # ── the whole record at each rung, against the indices ──────────────────
    con = duckdb.connect()
    idx = {}
    for iname, labels in CHAINS.items():
        d, lv = slice_from(*load_chain(con, labels), "2013-02-08")
        idx[iname] = curve_stats(d, lv)

    print("\n── full record, and the 2013+ window the indices cover ─────")
    print(f"  {'risk':>7} {'CAGR':>8} {'maxDD':>8} {'Sharpe':>7} {'Calmar':>7} "
          f"| {'2013+ CAGR':>10} {'2013+ DD':>9} {'beats':>22}")
    for rp, _cfg, res in keep:
        s = backtest.summarise(res)
        d, lv = slice_from(res.dates.astype("datetime64[D]"), res.equity, "2013-02-08")
        c = curve_stats(d, lv)
        beat = [n for n, v in idx.items() if c["cagr"] > v["cagr"]]
        print(f"  {rp:>6.2%} {s['cagr']:>8.2%} {s['max_dd']:>8.2%} "
              f"{s['sharpe']:>7.2f} {s['cagr']/abs(s['max_dd']):>7.2f} | "
              f"{c['cagr']:>10.2%} {c['max_dd']:>9.2%} {len(beat):>4} of 4")
    print("\n  the bar to clear, 2013-02-08 to date")
    for n, v in sorted(idx.items(), key=lambda kv: -kv[1]["cagr"]):
        print(f"    {n:<22} {v['cagr']:>7.2%}  DD {v['max_dd']:>7.2%}  "
              f"Sharpe {v['sharpe']:.2f}")

    # ── detail on the rungs inside the stated appetite ──────────────────────
    for rp, _cfg, res in keep:
        h = stats(res, "2024-01-01", None)
        if not (-0.20 <= h["max_dd"] <= -0.10):
            continue
        s = backtest.summarise(res)
        e = stats(res)
        yrs = backtest.by_year(res)
        neg = [y for y, r, _q, _d in yrs if r < 0]
        print(f"\n── risk {rp:.2%} ─────────────────────────────────────")
        print(f"  CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  Sharpe {s['sharpe']:.2f}  "
              f"Calmar {s['cagr']/abs(s['max_dd']):.2f}")
        print(f"  worst drawdown recovered in {e['recover']}d; underwater at most "
              f"{e['under']}d;  losing years {len(neg)} {neg}")
        print(f"  win {s['win_rate']:.2%}  payoff {s['payoff']:.3f}  "
              f"PF {s['profit_factor']:.3f}  expR {s['expectancy_r']:+.4f}  "
              f"hold {s['median_hold']:.0f}d  {s['n_trades']} trades")
        print(f"  exposure {s['exposure']:.2%} (p95 {e['peak_expo']:.1%})  "
              f"end Rs {s['end']:,.0f}")
        print("  year      return   worst DD")
        for y, r, _q, dd in yrs:
            print(f"    {y}   {r:>8.2%}   {dd:>8.2%}"
                  f"{'  <- held out' if y >= 2024 else ''}")
        ms = monthly(res)
        print("  best months : " + "  ".join(
            f"{m} {v:+.1%}" for m, v in sorted(ms, key=lambda x: -x[1])[:3]))
        print("  worst months: " + "  ".join(
            f"{m} {v:+.1%}" for m, v in sorted(ms, key=lambda x: x[1])[:3]))
        d2 = dict(ms)
        print("  2026: " + "  ".join(
            f"{m[-2:]} {d2[m]:+.1%}" for m in sorted(d2) if m.startswith("2026")))
        np.savez(os.path.join(os.path.dirname(INDEX), f"ladder_{int(rp*10000)}.npz"),
                 dates=res.dates.astype("datetime64[D]"), eq=res.equity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
