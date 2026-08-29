"""
Why 2022 and 2024 were flat, before trying to fix them.

Searching for parameters that repair two named years is how a backtest gets
curve-fitted: they are two observations, and any grid large enough will find
something that flatters them. So this looks for a *mechanism* first — if the
weakness has a cause that would recur, a fix aimed at the cause is worth
testing; if it does not, the years were simply bad and should be left alone.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import Engine

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    cfg = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                  max_per_group=1, require_sector_label=False, risk_pct=0.0040,
                  weekly_ema_exit=20, time_stop=10**6)
    feats = eng.feats(cfg)
    res = eng.run(cfg)

    yrs = md.dates.astype("datetime64[Y]").astype(int) + 1970
    reg = feats.regime
    ew = feats.ew_index

    print(f"  {'year':<6} {'return':>8} {'worstDD':>8} {'ON%':>6} {'flips':>6} "
          f"{'trades':>7} {'win':>6} {'meanRet':>8} {'mktON':>8} {'mktALL':>8}")
    by_year = {y: r for y, r, _e, _d in backtest.by_year(res)}
    ddmap = {y: d for y, _r, _e, d in backtest.by_year(res)}
    for y in range(2008, 2027):
        m = yrs == y
        if not m.any():
            continue
        r = reg[m]
        flips = int(np.count_nonzero(np.diff(r.astype(int)) != 0))
        d0, d1 = md.dates[m][0], md.dates[m][-1]
        tr = [p for p in res.trades if p.exit_date is not None and d0 <= p.exit_date <= d1]
        rets = np.array([p.ret for p in tr]) if tr else np.array([0.0])
        # what the market itself did, in total and only while the switch was on
        idx = np.flatnonzero(m)
        seg = ew[idx]
        mkt_all = seg[-1] / seg[0] - 1.0
        step = np.diff(np.log(ew))
        on_prev = reg[:-1]
        sel = np.zeros(len(step), bool)
        sel[idx[0]:idx[-1]] = True
        mkt_on = float(np.exp(step[sel & on_prev].sum()) - 1.0)
        print(f"  {y:<6} {by_year.get(y, 0):8.2%} {ddmap.get(y, 0):8.2%} "
              f"{r.mean():6.0%} {flips:6d} {len(tr):7d} "
              f"{np.mean(rets > 0):6.1%} {rets.mean():+8.2%} "
              f"{mkt_on:8.2%} {mkt_all:8.2%}")

    print("\n── exit reasons in the weak years vs the rest ──────────────")
    for label, years in (("2022 + 2024", {2022, 2024}),
                         ("every other year", set(range(2008, 2027)) - {2022, 2024})):
        tr = [p for p in res.trades if p.exit_date is not None
              and (p.exit_date.astype("datetime64[Y]").astype(int) + 1970) in years]
        c = Counter(p.reason for p in tr)
        n = sum(c.values()) or 1
        parts = "  ".join(f"{k} {v / n:.0%}" for k, v in c.most_common())
        wins = [p.ret for p in tr if p.ret > 0]
        print(f"  {label:<18} {n:5d} trades   {parts}")
        print(f"  {'':<18} win {len(wins) / n:.1%}   "
              f"mean win {np.mean(wins) if wins else 0:+.2%}   "
              f"mean loss {np.mean([p.ret for p in tr if p.ret <= 0]):+.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
