"""
Everything about one rung of the ladder, in the form the year table takes.

`ladder.py` prints detail only for rungs whose held-out drawdown lands inside a
band, which is right for choosing a rung and wrong once one has been chosen.
This takes a rung as an argument and reports it in full.

It adds one column the ladder does not have, and it is the one that answers
"how bad did this actually feel": the worst point of each year measured **from
that year's opening equity**, beside the conventional peak-to-trough drawdown.
The two differ whenever a year runs up before it falls -- 2024 fell 17.93% from
its February peak while never being more than 5.48% below where it started --
and a holder experiences the second number, not the first.

    PULSE_RUNG=0.008 PULSE_BARS=... PULSE_INDEX=... uv run python scripts/rung.py
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
from pipeline.compute.strategy.config import preset

from scripts.aggressive import HOLD, TRAIN_END, monthly, stats
from scripts.benchmarks import CHAINS, curve_stats, load_chain, slice_from
from scripts.walkforward import Engine, period

BARS = os.environ["PULSE_BARS"]
INDEX = os.environ["PULSE_INDEX"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")
RUNG = float(os.environ.get("PULSE_RUNG", "0.008"))


def episodes(dates: np.ndarray, eq: np.ndarray, n: int = 6) -> List[Tuple]:
    """The n deepest peak-to-trough falls, with how long each took to make back."""
    run = np.maximum.accumulate(eq)
    dd = eq / run - 1.0
    out: List[Tuple] = []
    used = np.zeros(len(eq), bool)
    for _ in range(n):
        cand = np.where(used, 0.0, dd)
        t = int(np.argmin(cand))
        if cand[t] >= -1e-9:
            break
        p = int(np.flatnonzero(eq[:t + 1] == run[t])[-1])
        rec = np.flatnonzero((np.arange(len(eq)) > t) & (eq >= run[t]))
        r = int(rec[0]) if rec.size else -1
        end = r if r > 0 else len(eq) - 1
        out.append((dates[p], dates[t], dates[end] if r > 0 else None,
                    float(dd[t]), t - p, (end - t)))
        used[p:end + 1] = True
    return out


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    cfg = replace(preset("balanced"),
                  sector_top_frac=0.0, max_per_sector=0, max_per_group=1,
                  require_sector_label=False, time_stop=10 ** 6,
                  cash_yield=0.0, weekly_ema_exit=20, regime_ma=100,
                  stop_atr=3.0, max_positions=12, max_weight=0.125,
                  risk_pct=RUNG)

    tr = stats(eng.run(cfg, end=TRAIN_END))
    res = eng.run(cfg)
    s = backtest.summarise(res)
    e = stats(res)
    h = stats(res, "2024-01-01", None)
    d = res.dates.astype("datetime64[D]")
    eq = res.equity

    print(f"══ risk {RUNG:.2%} per trade, 0% on idle cash ══════════════")
    print(f"  full record  CAGR {s['cagr']:.2%}   maxDD {s['max_dd']:.2%}   "
          f"Sharpe {s['sharpe']:.2f}   Calmar {s['cagr']/abs(s['max_dd']):.2f}")
    print(f"  TRAIN 08-23  CAGR {tr['cagr']:.2%}   maxDD {tr['max_dd']:.2%}")
    print(f"  HELD OUT     CAGR {h['cagr']:.2%}   maxDD {h['max_dd']:.2%}   "
          f"inflation {abs(h['max_dd'])/abs(tr['max_dd']):.2f}x")
    print(f"  win {s['win_rate']:.2%}  payoff {s['payoff']:.3f}  "
          f"PF {s['profit_factor']:.3f}  expR {s['expectancy_r']:+.4f}  "
          f"hold {s['median_hold']:.0f}d  {s['n_trades']} trades")
    print(f"  exposure {s['exposure']:.2%} (p95 {e['peak_expo']:.1%})   "
          f"Rs 50L -> Rs {s['end']:,.0f}")

    print("\n── nineteen years ──────────────────────────────────────────")
    print(f"  {'year':<6} {'return':>9} {'worst DD':>10} {'worst vs Jan 1':>15}")
    yrs = res.dates.astype("datetime64[Y]").astype(int) + 1970
    neg = 0
    for y, r, _q, dd in backtest.by_year(res):
        idx = np.flatnonzero(yrs == y)
        base = eq[idx[0] - 1] if idx[0] > 0 else res.capital
        vs_open = float((eq[idx] / base - 1.0).min())
        neg += r < 0
        print(f"  {y:<6} {r:>+9.2%} {dd:>10.2%} {vs_open:>15.2%}"
              f"{'   <- held out' if y >= 2024 else ''}")
    print(f"  losing years: {neg} of 19")

    print("\n── the deepest falls, and how long each took to make back ──")
    print(f"  {'peak':<12} {'trough':<12} {'recovered':<12} {'depth':>8} "
          f"{'down':>6} {'back':>6}")
    for p, t, r, depth, down, back in episodes(d, eq):
        print(f"  {p!s:<12} {t!s:<12} "
              f"{(str(r) if r is not None else 'still under'):<12} "
              f"{depth:>8.2%} {down:>5}d {back:>5}d")

    ms = dict(monthly(res))
    for yr in ("2024", "2025", "2026"):
        cols = [m for m in sorted(ms) if m.startswith(yr)]
        print(f"\n  {yr} monthly:  " + "  ".join(
            f"{m[-2:]} {ms[m]:+.1%}" for m in cols))
        print(f"    year {period(res, f'{yr}-01-01', f'{int(yr)+1}-01-01')['ret']:+.2%}")

    allm = monthly(res)
    print("\n  best months : " + "  ".join(
        f"{m} {v:+.1%}" for m, v in sorted(allm, key=lambda x: -x[1])[:4]))
    print("  worst months: " + "  ".join(
        f"{m} {v:+.1%}" for m, v in sorted(allm, key=lambda x: x[1])[:4]))
    pos = sum(1 for _m, v in allm if v > 0)
    print(f"  positive months: {pos} of {len(allm)} ({pos/len(allm):.0%})")

    con = duckdb.connect()
    dd_, lv = slice_from(d, eq, "2013-02-08")
    c = curve_stats(dd_, lv)
    print("\n── vs the indices, 2013-02-08 to date ──────────────────────")
    print(f"  {'series':<22} {'CAGR':>8} {'total':>9} {'maxDD':>8} {'Sharpe':>7}")
    print(f"  {'this rung':<22} {c['cagr']:>8.2%} {c['total']:>9.1%} "
          f"{c['max_dd']:>8.2%} {c['sharpe']:>7.2f}")
    for iname, labels in CHAINS.items():
        di, li = slice_from(*load_chain(con, labels), "2013-02-08")
        v = curve_stats(di, li)
        print(f"  {iname:<22} {v['cagr']:>8.2%} {v['total']:>9.1%} "
              f"{v['max_dd']:>8.2%} {v['sharpe']:>7.2f}")

    ys = [period(res, a, b)["ret"] for _l, a, b in HOLD]
    print(f"\n  held out: 2024 {ys[0]:+.2%}   2025 {ys[1]:+.2%}   2026 {ys[2]:+.2%}"
          f"   -> {h['cagr']:.2%} annualised")
    np.savez(os.path.join(os.path.dirname(INDEX), f"rung_{int(RUNG*10000)}.npz"),
             dates=d, eq=eq)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
