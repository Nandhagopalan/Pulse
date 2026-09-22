"""
Why a sideways year was flat, and what capping fills per session does about it.

2022 returned +3.7% at the traded rung while the market it trades went nowhere.
The cause is not selection: the regime switch closes the whole book at once, so
the session after every turn spent all twelve slots on one morning's ranked
list, and the next turn sold them. In 2022 the switch flipped twelve times and
72 of 85 exits were regime exits.

`max_new_per_session` caps how many of those slots may be filled in one session.
This script is the evidence for the value the `paced` preset ships with, and it
is deliberately arranged so the result can be disbelieved:

  1. the diagnosis -- flips, exit reasons, and whether the year's winners were
     ever on the ranked list at all
  2. the cap grid, to show a shelf rather than a spike
  3. three unrelated ways of expressing the same pacing
  4. the control that matters -- the same cap taking *worse* candidates, which
     is what separates "the day's strongest" from "trade less"
  5. selection on 2008-2021 alone, by walkforward's declared criterion, so
     neither 2022 nor the held-out years choose the parameter

    PULSE_BARS=/path/bars.parquet PULSE_CONSTITUENTS=/path/constituents.parquet \
    uv run python -m scripts.entry_pacing
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import replace
from typing import Callable, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data, rules
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import Engine

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

TRAIN_END = "2022-01-01"   # 2022 itself is never in the selection window
CRITERION = "calmar"       # CAGR / |max drawdown|, as walkforward declares it

BASE = preset("deployed")
HEAD = (f"  {'variant':<38} {'CAGR':>7} {'maxDD':>8} {'2022':>7} "
        f"{'2024':>7} {'2025':>7} {'2026':>7} {'trades':>7}")


def row(label: str, res: backtest.Result) -> str:
    s = backtest.summarise(res)
    y = {yr: r for yr, r, _e, _d in backtest.by_year(res)}
    return (f"  {label:<38} {s['cagr']:7.2%} {s['max_dd']:8.2%} "
            f"{y.get(2022, 0):+7.1%} {y.get(2024, 0):+7.1%} "
            f"{y.get(2025, 0):+7.1%} {y.get(2026, 0):+7.1%} {s['n_trades']:7d}")


def taking(pick: Callable[[list], list]):
    """Run with `entry_candidates` filtered — the control harness."""
    original = rules.entry_candidates

    def patched(md, feats, cfg, t, exclude=()):
        return pick(original(md, feats, cfg, t, exclude))
    return original, patched


def run_filtered(eng: Engine, cfg: StrategyConfig,
                 pick: Optional[Callable[[list], list]] = None) -> backtest.Result:
    if pick is None:
        return eng.run(cfg)
    original, patched = taking(pick)
    rules.entry_candidates = patched
    try:
        return eng.run(cfg)
    finally:
        rules.entry_candidates = original


def diagnose(md, eng: Engine) -> None:
    """The mechanism, before any parameter is touched."""
    feats = eng.feats(BASE)
    res = eng.run(BASE)
    yrs = md.dates.astype("datetime64[Y]").astype(int) + 1970
    in22 = yrs == 2022
    flips = int(np.count_nonzero(np.diff(feats.regime[in22].astype(int)) != 0))
    tr = [p for p in res.trades
          if p.entry_date is not None
          and np.datetime64("2022-01-01") <= p.entry_date < np.datetime64("2023-01-01")]
    reasons = Counter(p.reason for p in tr)
    print(f"\n  2022: {flips} regime flips, regime on {feats.regime[in22].mean():.0%} "
          f"of sessions, {len(tr)} entries")
    print("  exit reasons: " + "  ".join(f"{k} {v}" for k, v in reasons.most_common()))

    # Were the year's winners ever on the ranked list while the book could buy?
    idx = np.flatnonzero(in22)
    t0, t1 = idx[0] - 1, idx[-1]
    with np.errstate(all="ignore"):
        year_ret = md.close[t1] / md.close[t0] - 1.0
    cols = np.flatnonzero(feats.universe[t0] & np.isfinite(year_ret))
    held = {p.col for p in tr}
    print(f"  universe {cols.size} names, median {np.median(year_ret[cols]):+.1%}, "
          f"{np.mean(year_ret[cols] > 0.20):.0%} rose more than 20%")
    print(f"  {'the ten best':<14} {'return':>8} {'signals':>8} {'while on':>9}  held")
    for j in cols[np.argsort(-year_ret[cols])][:10]:
        sig = on = 0
        for t in idx:
            c = md.close[t, j]
            ok = bool(feats.universe[t, j] and c > feats.prior_hi[t, j]
                      and c > feats.sma50[t, j] and feats.sma50[t, j] > feats.sma150[t, j]
                      and feats.sma150[t, j] > feats.sma200[t, j] and feats.s200_rising[t, j]
                      and feats.rs_pct[t, j] >= BASE.rs_min_pct)
            sig += ok
            on += ok and bool(feats.regime[t])
        print(f"  {md.symbols[j]!s:<14} {year_ret[j]:+8.1%} {sig:8d} {on:9d}  "
              f"{'yes' if j in held else 'no'}")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)

    print("── 1. the mechanism ────────────────────────────────────────")
    diagnose(md, eng)

    print("\n── 2. the cap grid: a shelf, not a spike ───────────────────")
    print(HEAD)
    for cap in (0, 1, 2, 3, 4, 5, 6, 8):
        label = "uncapped (as traded)" if cap == 0 else f"cap {cap}/session"
        print(row(label, eng.run(replace(BASE, max_new_per_session=cap))), flush=True)

    print("\n── 3. the same pacing, expressed differently ───────────────")
    print(HEAD)
    # If the damage really is concentrated at the turns, capping *only* there
    # should reproduce the gain of capping always. `on_run` counts consecutive
    # ON sessions, so this caps the first ten sessions of each regime and
    # leaves a long trend uncapped.
    original = rules.entry_candidates

    def only_after_turn(md_, feats_, cfg_, t, exclude=()):
        out = original(md_, feats_, cfg_, t, exclude)
        if feats_.on_run is not None and feats_.on_run[t] <= 10:
            return out[:3]
        return out
    rules.entry_candidates = only_after_turn
    try:
        print(row("cap 3 only within 10d of a turn", eng.run(BASE)), flush=True)
    finally:
        rules.entry_candidates = original

    print("\n── 4. the control: pacing is not the whole story ───────────")
    print(HEAD)
    print(row("cap 2, the day's strongest two",
              eng.run(replace(BASE, max_new_per_session=2))), flush=True)
    for skip in (2, 4):
        print(row(f"cap 2, ranks {skip + 1}-{skip + 2} instead",
                  run_filtered(eng, BASE, lambda c, s=skip: c[s:s + 2])), flush=True)
    for seed in (1, 2, 3):
        rng = np.random.default_rng(seed)

        def two_at_random(c, rng=rng):
            if len(c) <= 2:
                return c
            return [c[i] for i in sorted(rng.choice(len(c), 2, replace=False))]
        print(row(f"cap 2, two at random (seed {seed})",
                  run_filtered(eng, BASE, two_at_random)), flush=True)

    print("\n── 5. selection on 2008-2021 only ──────────────────────────")
    print(f"  criterion fixed before the sweep: {CRITERION}, on sessions < {TRAIN_END}")
    scored: List[tuple] = []
    for cap in (0, 1, 2, 3, 4, 5, 6, 8):
        cfg = replace(BASE, max_new_per_session=cap)
        tr = backtest.summarise(eng.run(cfg, end=TRAIN_END))
        cal = tr["cagr"] / abs(tr["max_dd"]) if tr["max_dd"] < 0 else 0.0
        full = {y: r for y, r, _e, _d in backtest.by_year(eng.run(cfg))}
        scored.append((cal, cap, tr, full))
        print(f"  cap {cap:<2} TRAIN CAGR {tr['cagr']:6.2%} maxDD {tr['max_dd']:7.2%} "
              f"calmar {cal:4.2f}  ->  then 2022 {full.get(2022, 0):+6.1%}  "
              f"2024 {full.get(2024, 0):+6.1%} 2025 {full.get(2025, 0):+6.1%} "
              f"2026 {full.get(2026, 0):+6.1%}", flush=True)
    cal, cap, _tr, full = max(scored, key=lambda s: s[0])
    print(f"\n  training picks cap {cap} (calmar {cal:.2f}); it then returned "
          f"{full.get(2022, 0):+.1%} in 2022 and "
          f"{full.get(2024, 0):+.1%} / {full.get(2025, 0):+.1%} / "
          f"{full.get(2026, 0):+.1%} in the held-out years.")
    print("  That is the value `paced` ships with.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
