"""
How fast the book can get out, and what that buys at full deployment.

The first aggressive sweep found the shape of the problem but not the answer.
Holding entries fixed and only re-fitting size, the frontier ran from 9.2% CAGR
at -16% drawdown to 17.7% at -29%, with nothing in between: a declared 13%
budget matched no configuration at all. Sizing alone cannot separate return
from risk here, because at full deployment the drawdown is not set by position
size -- it is set by how long the book stays invested after the market turns.

So this sweeps the two things that control that, which the sizing study held
constant:

  * **`regime_ma`** -- the equal-weight index is judged against its own moving
    average, and that average's length is the rollback delay. 50 sessions turns
    weeks earlier than 100 and whipsaws more; the trade is real and untested.
  * **`stop_atr`** -- how far a single position runs against the book before it
    is cut, which is the defence between regime flips.

Selection rule, declared before the run: inside a drawdown budget, require the
worst drawdown to have been recovered within 252 sessions, then take the
highest CAGR. The recovery clause is the point -- a 20% fall made back inside a
year is the stated risk appetite; the same 20% taking three years is not.

TRAIN is 2008-2023. 2024-2026 are scored once, on frozen configs.

    PULSE_BARS=... PULSE_INDEX=... uv run python scripts/rollback.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, List, Tuple

import duckdb

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

MIN_TRADES = 400
MAX_RECOVERY = 252              # sessions; a drawdown must be made back inside a year
BUDGETS = [0.15, 0.18, 0.20]


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    base = replace(preset("balanced"),
                   sector_top_frac=0.0, max_per_sector=0, max_per_group=1,
                   require_sector_label=False, time_stop=10 ** 6,
                   cash_yield=0.0, weekly_ema_exit=20)

    scored: List[Tuple[str, StrategyConfig, dict, int]] = []
    for rma in (50, 75, 100, 150):
        for rp in (0.006, 0.010, 0.020):
            for mp in (12, 15, 20):
                for mw in (0.08, 0.10, 0.125):
                    for sa in (2.0, 2.5, 3.0):
                        cfg = replace(base, regime_ma=rma, risk_pct=rp,
                                      max_positions=mp, max_weight=mw, stop_atr=sa)
                        tag = (f"ma{rma} r{rp*100:.1f}% n{mp} "
                               f"w{mw*100:.0f}% s{sa:.1f}")
                        res = eng.run(cfg, end=TRAIN_END)
                        scored.append((tag, cfg, stats(res), len(res.trades)))
        print(f"    [regime_ma {rma}] {len(scored)} scored", flush=True)

    ok = [r for r in scored if r[3] >= MIN_TRADES]
    print(f"\n── {len(ok)} of {len(scored)} configs cleared {MIN_TRADES} trades ──")

    print("\n  best TRAIN CAGR at each rollback speed, drawdown unconstrained")
    print(f"  {'config':<28} {'CAGR':>7} {'maxDD':>7} {'recov':>6} {'under':>6} "
          f"{'expo':>6} {'p95':>6}")
    for rma in (50, 75, 100, 150):
        grp = [r for r in ok if r[0].startswith(f"ma{rma} ")]
        t, _c, s, _n = max(grp, key=lambda r: r[2]["cagr"])
        print(f"  {t:<28} {s['cagr']:>7.2%} {s['max_dd']:>7.2%} {s['recover']:>6} "
              f"{s['under']:>6} {s['expo']:>6.1%} {s['peak_expo']:>6.1%}")

    print(f"\n── frozen picks: max TRAIN CAGR inside budget, recovery <= "
          f"{MAX_RECOVERY}d ──")
    picks: List[Tuple[str, str, StrategyConfig]] = []
    for b in BUDGETS:
        inside = [r for r in ok
                  if r[2]["max_dd"] >= -b and r[2]["recover"] <= MAX_RECOVERY]
        if not inside:
            print(f"  budget {b:.0%}: nothing qualifies")
            continue
        t, c, s, n = max(inside, key=lambda r: r[2]["cagr"])
        picks.append((f"DD<={b:.0%}", t, c))
        print(f"  budget {b:>4.0%}  ->  {t}   ({len(inside)} qualified)")
        print(f"       TRAIN CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  "
              f"recovered in {s['recover']}d  underwater <= {s['under']}d  "
              f"expo {s['expo']:.1%} (p95 {s['peak_expo']:.1%})  {n} trades")

    print("\n── 2024-2026, never seen during selection ──────────────────")
    print(f"  {'book':<12} {'2024':>9} {'2025':>9} {'2026':>9} {'3y CAGR':>9} "
          f"{'3y DD':>8} {'recov':>6} {'expo':>6}")
    full: Dict[str, backtest.Result] = {}
    for name, _tag, cfg in picks:
        res = eng.run(cfg)
        full[name] = res
        ys = [period(res, a, b)["ret"] for _l, a, b in HOLD]
        h = stats(res, "2024-01-01", None)
        print(f"  {name:<12} {ys[0]:>9.2%} {ys[1]:>9.2%} {ys[2]:>9.2%} "
              f"{h['cagr']:>9.2%} {h['max_dd']:>8.2%} {h['recover']:>6} "
              f"{h['expo']:>6.1%}")

    for name, tag, _cfg in picks:
        res = full[name]
        s = backtest.summarise(res)
        e = stats(res)
        yrs = backtest.by_year(res)
        neg = [y for y, r, _q, _d in yrs if r < 0]
        print(f"\n── {name}: {tag} ─────────────────────────────")
        print(f"  CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  Sharpe {s['sharpe']:.2f} "
              f"Calmar {s['cagr']/abs(s['max_dd']):.2f}  losing years {len(neg)} {neg}")
        print(f"  worst drawdown recovered in {e['recover']}d; underwater at most "
              f"{e['under']}d")
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
        print(f"  2026-04: {dict(ms).get('2026-04', float('nan')):+.2%}")

    con = duckdb.connect()
    print("\n── vs the indices, 2013-02-08 to date ──────────────────────")
    print(f"  {'series':<22} {'CAGR':>8} {'total':>9} {'maxDD':>8} {'Sharpe':>7} "
          f"{'Calmar':>7}")
    rows = []
    for name, _tag, _cfg in picks:
        res = full[name]
        d, lv = slice_from(res.dates.astype("datetime64[D]"), res.equity, "2013-02-08")
        rows.append((f"Pulse {name}", curve_stats(d, lv)))
    for iname, labels in CHAINS.items():
        d, lv = slice_from(*load_chain(con, labels), "2013-02-08")
        rows.append((iname, curve_stats(d, lv)))
    for nm, st in rows:
        cal = st["cagr"] / abs(st["max_dd"]) if st["max_dd"] < 0 else 0.0
        print(f"  {nm:<22} {st['cagr']:>8.2%} {st['total']:>9.1%} "
              f"{st['max_dd']:>8.2%} {st['sharpe']:>7.2f} {cal:>7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
