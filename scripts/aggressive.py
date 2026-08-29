"""
The book rebuilt for a trader who will not park the float, and will wear 20%.

Three constraints changed, and all three push the same way:

  * **No cash yield.** The published book earned 5% on a balance that sat ~82%
    idle, which was 4.17 of its 10.01 CAGR points. Set that to zero and the
    same trades compound at 5.84%. Idle capital is now dead weight, so the
    optimiser has an entirely different problem to solve.
  * **Deploy hard when the regime is on.** Sizing was ATR-risk-first at 0.25%,
    which is what held exposure near 18%. Raising `risk_pct` until `max_weight`
    binds instead converts the book from risk-parity to near-equal-weight, and
    the position count times the weight cap sets how close to fully invested it
    gets.
  * **A 15-20% drawdown is acceptable if it recovers fast.** So depth alone is
    no longer the constraint. This measures recovery too: the longest stretch
    underwater, and how long the worst drawdown took to make back.

Nothing about the *entry* logic is re-searched here. The rules that survived
the earlier holdouts -- 52-week breakout, trend template, cross-sectional
momentum, the 100-day regime switch, the weekly EMA20 trail, no time stop --
are carried over untouched. Only sizing and deployment are re-fitted, which is
what the change of objective actually calls for, and it keeps the number of
things fitted to this lake small.

Selection sees 2008-2023 and nothing else. 2024, 2025 and 2026 are scored once,
at the end, on configs already frozen.

    PULSE_BARS=... PULSE_INDEX=... uv run python scripts/aggressive.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import duckdb
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.benchmarks import CHAINS, curve_stats, load_chain, slice_from
from scripts.walkforward import Engine, period

BARS = os.environ["PULSE_BARS"]
INDEX = os.environ["PULSE_INDEX"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

TRAIN_END = "2024-01-01"        # exclusive; last training session is 2023-12-31
HOLD = [("2024", "2024-01-01", "2025-01-01"),
        ("2025", "2025-01-01", "2026-01-01"),
        ("2026", "2026-01-01", None)]
MIN_TRADES = 400                # over TRAIN; below this the sample is not evidence

# Budgets declared before the sweep runs, so the pick inside each cannot be
# chosen after seeing which one the holdout happens to like. Drawdown ran
# 1.25-1.70x deeper out of sample in the earlier three-year test, so a 20%
# live ceiling means a train budget nearer 13%.
BUDGETS = [0.10, 0.13, 0.16]


def underwater(eq: np.ndarray, dates: np.ndarray) -> Tuple[int, int]:
    """
    (longest stretch underwater, sessions the worst drawdown took to recover).

    Depth is only half of what a drawdown costs. A 20% fall that is made back
    inside a quarter is a different experience from the same 20% that takes
    three years, and the second is what makes people abandon a system. Recovery
    is still running at the end of the record when the book has not yet made a
    new high; that is reported as the sessions elapsed so far, not as zero.
    """
    peak = np.maximum.accumulate(eq)
    under = eq < peak
    longest = run = 0
    for u in under:
        run = run + 1 if u else 0
        longest = max(longest, run)
    trough = int(np.argmin(eq / peak - 1.0))
    rec = np.flatnonzero((np.arange(len(eq)) > trough) & (eq >= peak[trough]))
    to_rec = int(rec[0] - trough) if rec.size else int(len(eq) - 1 - trough)
    return longest, to_rec


def stats(res: backtest.Result, start: Optional[str] = None,
          end: Optional[str] = None) -> dict:
    d = res.dates
    lo = int(np.searchsorted(d, np.datetime64(start))) if start else 0
    hi = int(np.searchsorted(d, np.datetime64(end))) if end else len(d)
    eq, dts = res.equity[lo:hi], d[lo:hi]
    years = (dts[-1] - dts[0]).astype("timedelta64[D]").astype(float) / 365.25
    dd = eq / np.maximum.accumulate(eq) - 1.0
    total = float(np.prod(res.twr[lo:hi]) - 1.0)
    longest, to_rec = underwater(eq, dts)
    return {
        "cagr": float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0.3 else total,
        "total": total,
        "max_dd": float(dd.min()),
        "under": longest,
        "recover": to_rec,
        "expo": float(res.invested[lo:hi].mean()),
        "peak_expo": float(np.quantile(res.invested[lo:hi], 0.95)),
    }


def monthly(res: backtest.Result) -> List[Tuple[str, float]]:
    """Month-end chain-linked returns, for reading how fast the book moves."""
    m = res.dates.astype("datetime64[M]")
    out = []
    for um in np.unique(m):
        idx = np.flatnonzero(m == um)
        out.append((str(um), float(np.prod(res.twr[idx]) - 1.0)))
    return out


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)

    # Everything the earlier holdouts validated, with the yield switched off.
    base = replace(preset("balanced"),
                   sector_top_frac=0.0, max_per_sector=0, max_per_group=1,
                   require_sector_label=False, time_stop=10 ** 6,
                   cash_yield=0.0)

    grid: List[Tuple[str, StrategyConfig]] = []
    for wk in (20, None):
        for rp in (0.006, 0.008, 0.010, 0.0125, 0.015, 0.020, 0.030):
            for mp in (10, 12, 15, 20):
                for mw in (0.08, 0.10, 0.125, 0.15):
                    tag = f"wk{wk or '-'} r{rp*100:.2f}% n{mp} w{mw*100:.0f}%"
                    grid.append((tag, replace(base, weekly_ema_exit=wk,
                                              risk_pct=rp, max_positions=mp,
                                              max_weight=mw)))
    print(f"── {len(grid)} configs, scored on TRAIN 2008-01..2023-12 only ──")

    scored: List[Tuple[str, StrategyConfig, dict, int]] = []
    for tag, cfg in grid:
        res = eng.run(cfg, end=TRAIN_END)
        s = stats(res)
        scored.append((tag, cfg, s, len(res.trades)))

    ok = [(t, c, s, n) for t, c, s, n in scored if n >= MIN_TRADES]
    print(f"  {len(ok)} of {len(scored)} cleared the {MIN_TRADES}-trade floor\n")

    print("  the ten highest-CAGR configs on TRAIN, whatever their drawdown")
    print(f"  {'config':<30} {'CAGR':>7} {'maxDD':>7} {'under':>6} {'recov':>6} "
          f"{'expo':>6} {'p95':>6} {'trades':>6}")
    for t, _c, s, n in sorted(ok, key=lambda r: -r[2]["cagr"])[:10]:
        print(f"  {t:<30} {s['cagr']:>7.2%} {s['max_dd']:>7.2%} {s['under']:>6} "
              f"{s['recover']:>6} {s['expo']:>6.1%} {s['peak_expo']:>6.1%} {n:>6}")

    # ── freeze one config per declared budget ───────────────────────────────
    picks: List[Tuple[str, str, StrategyConfig]] = []
    print("\n── frozen picks: highest TRAIN CAGR inside each declared budget ──")
    for b in BUDGETS:
        inside = [r for r in ok if r[2]["max_dd"] >= -b]
        if not inside:
            print(f"  budget {b:.0%}: nothing qualifies")
            continue
        t, c, s, n = max(inside, key=lambda r: r[2]["cagr"])
        picks.append((f"DD<={b:.0%}", t, c))
        print(f"  budget {b:>4.0%}  ->  {t}")
        print(f"       TRAIN CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  "
              f"underwater {s['under']}d  recovered in {s['recover']}d  "
              f"expo {s['expo']:.1%} (p95 {s['peak_expo']:.1%})  {n} trades")

    # ── the held-out years, seen for the first time ─────────────────────────
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

    # ── full record on each frozen pick ─────────────────────────────────────
    for name, tag, _cfg in picks:
        res = full[name]
        s = backtest.summarise(res)
        e = stats(res)
        print(f"\n── {name}: {tag} ─────────────────────────────")
        print(f"  CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  Sharpe {s['sharpe']:.2f} "
              f"Calmar {s['cagr']/abs(s['max_dd']):.2f}")
        print(f"  underwater at most {e['under']} sessions; worst drawdown "
              f"recovered in {e['recover']}")
        print(f"  win {s['win_rate']:.2%}  payoff {s['payoff']:.3f}  "
              f"PF {s['profit_factor']:.3f}  expR {s['expectancy_r']:+.4f}  "
              f"hold {s['median_hold']:.0f}d  {s['n_trades']} trades")
        print(f"  exposure {s['exposure']:.2%} (p95 {e['peak_expo']:.1%})  "
              f"end Rs {s['end']:,.0f}")
        print("  year      return   worst DD")
        for y, r, _eq, dd in backtest.by_year(res):
            flag = "  <- held out" if y >= 2024 else ""
            print(f"    {y}   {r:>8.2%}   {dd:>8.2%}{flag}")
        ms = monthly(res)
        best = sorted(ms, key=lambda x: -x[1])[:3]
        worst = sorted(ms, key=lambda x: x[1])[:3]
        print("  best months : " + "  ".join(f"{m} {v:+.1%}" for m, v in best))
        print("  worst months: " + "  ".join(f"{m} {v:+.1%}" for m, v in worst))
        apr = dict(ms).get("2026-04")
        if apr is not None:
            print(f"  2026-04: {apr:+.2%}")

    # ── against the indices, on the window they cover ───────────────────────
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
