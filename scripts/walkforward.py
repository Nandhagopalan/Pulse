"""
Out-of-sample validation: choose parameters on <= 2024, then look at 2025-26.

The full-period figures in docs/strategy-engine.md have one weakness that no
amount of in-sample robustness checking can fix: the `balanced` preset was
selected while looking at the whole lake, 2025 and 2026 included. Any judgement
about how it "is doing this year" made against that preset is therefore partly
a judgement about a number that was fitted to the year in question.

This script severs that. It splits the lake:

    TRAIN   2008-01-16 .. 2024-12-31   parameters may be chosen here
    OOS-1   2025-01-01 .. 2025-12-31   never seen by the selection
    OOS-2   2026-01-01 .. latest       never seen by the selection

and reports three things:

  1. a leakage proof -- features over the truncated lake equal features over the
     full lake, row for row, across the overlap. If that ever fails, some rule
     is reading forward and every number below is fiction.
  2. what a sweep restricted to TRAIN picks, and how that pick then does on the
     two held-out periods.
  3. the *distribution* of holdout results over the entire sweep. This is the
     part that actually answers "am I overfitting": if only the train-optimum
     survives out of sample the edge is a fit, and if most of the grid survives
     it is not.

    PULSE_BARS=/path/bars.parquet PULSE_CONSTITUENTS=/path/constituents.parquet \
    uv run python scripts/walkforward.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import OrderedDict
from dataclasses import fields, replace
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data, rules
from pipeline.compute.strategy.config import StrategyConfig, preset

BARS = os.environ.get("PULSE_BARS")
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")

TRAIN_END = "2025-01-01"     # exclusive: the last training session is 2024-12-31
OOS1 = ("2025-01-01", "2026-01-01")
OOS2 = ("2026-01-01", None)  # to the end of the lake

CAPITAL = 5_000_000.0

# Selection rule, fixed before the sweep is run so that it cannot be chosen
# after seeing which config it happens to favour.
MIN_TRADES = 300             # over TRAIN; below this the sample is not evidence
CRITERION = "calmar"         # CAGR / |max drawdown|

# Fields `compute_features` reads. Two configs agreeing on all of these share a
# feature set, which is what makes the sweep affordable: features cost ~130s,
# a backtest run over them costs 0.6s.
_FEATURE_FIELDS = (
    "series", "min_turnover", "turnover_window", "top_n_turnover", "min_price",
    "min_history", "regime_index_n", "regime_ma", "breakout_lookback",
    "trend_slope_window", "rs_lookback", "sector_top_frac", "sector_lookback",
    "atr_len", "adv_window", "ema_exit", "use_volume_filter", "volume_mult",
    "require_contraction", "equity_only", "require_sector_label",
)


def feature_key(cfg: StrategyConfig) -> tuple:
    return tuple(getattr(cfg, f) for f in _FEATURE_FIELDS)


class Engine:
    """
    Owns the bars and a small feature cache.

    The cache is an LRU of two, not a dictionary of everything. A feature set is
    ~800 MB over this lake and the sweep touches eight of them; holding them all
    costs more memory than the machine has, and the swapping that follows is
    slower than simply recomputing. Two is enough because every caller below
    walks configurations grouped by feature key, so the working set is one
    feature block plus the baseline being compared against.
    """

    CACHE = 2

    def __init__(self, md: rules.MarketData) -> None:
        self.md = md
        self._cache: "OrderedDict[tuple, rules.Features]" = OrderedDict()
        self.misses = 0

    def feats(self, cfg: StrategyConfig) -> rules.Features:
        key = feature_key(cfg)
        hit = self._cache.pop(key, None)
        if hit is None:
            self.misses += 1
            t = time.time()
            hit = rules.compute_features(self.md, cfg)
            print(f"    [features #{self.misses}] {time.time() - t:.0f}s", flush=True)
        self._cache[key] = hit
        while len(self._cache) > self.CACHE:
            self._cache.popitem(last=False)
        return hit

    def run(self, cfg: StrategyConfig, start: Optional[str] = None,
            end: Optional[str] = None, capital: float = CAPITAL) -> backtest.Result:
        return backtest.run(self.md, self.feats(cfg), cfg, capital=capital,
                            start=start, end=end)


# ── period arithmetic ───────────────────────────────────────────────────────
def period(res: backtest.Result, start: Optional[str], end: Optional[str]) -> dict:
    """
    Return, drawdown and activity over a slice of one continuous run.

    The return is chain-linked from `res.twr` rather than taken as an equity
    ratio. On a book with no external flows the two agree; taking the daily
    factors keeps it correct if one is ever introduced, and it is the same
    quantity `backtest.summarise` reports as CAGR.
    """
    d = res.dates
    lo = int(np.searchsorted(d, np.datetime64(start))) if start else 0
    hi = int(np.searchsorted(d, np.datetime64(end))) if end else len(d)
    if hi <= lo:
        return {"n": 0}
    tw, eq = res.twr[lo:hi], res.equity[lo:hi]
    dd = eq / np.maximum.accumulate(eq) - 1.0
    years = (d[hi - 1] - d[lo]).astype("timedelta64[D]").astype(float) / 365.25
    total = float(np.prod(tw) - 1.0)
    d0, d1 = d[lo], d[hi - 1]
    closed = [p for p in res.trades
              if p.exit_date is not None and d0 <= p.exit_date <= d1]
    return {
        "n": hi - lo,
        "from": str(d[lo]), "to": str(d[hi - 1]),
        "years": years,
        "ret": total,
        "cagr": float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0.3 else total,
        "max_dd": float(dd.min()),
        "exposure": float(res.invested[lo:hi].mean()),
        "end_equity": float(eq[-1]),
        "n_closed": len(closed),
    }


def score(s: dict) -> float:
    if s["n_trades"] < MIN_TRADES:
        return -1e9
    return s["cagr"] / abs(s["max_dd"]) if s["max_dd"] < 0 else 0.0


# ── the sweep space, declared up front ──────────────────────────────────────
# Every value tried is listed here. The count matters: the more configurations
# are compared on TRAIN, the more the best one owes to luck, and reporting the
# grid honestly is what lets the holdout numbers be read at face value.
GRID: Dict[str, list] = {
    # sizing and concentration
    "risk_pct":         [0.0040, 0.0050, 0.0060, 0.0080, 0.0100],
    "max_positions":    [8, 10, 12, 15, 20],
    "max_weight":       [0.06, 0.08, 0.10, 0.12, 0.15],
    # entry selectivity
    "rs_min_pct":       [0.70, 0.75, 0.80, 0.85, 0.90],
    "trend_template":   [True, False],
    "max_per_sector":   [2, 3, 4, 0],
    # exits
    "stop_atr":         [2.0, 2.5, 3.0, 3.5, 4.0],
    "time_stop":        [40, 60, 90, 120],
    "trail_atr":        [None, 2.5, 3.5],
    # feature-affecting (each new value costs a full feature rebuild)
    "breakout_lookback": [120, 250],
    "regime_ma":        [50, 100, 150],
    "rs_lookback":      [63, 126, 252],
    "sector_top_frac":  [0.0, 0.25, 0.50],
}


def coordinate_sweep(eng: Engine, base: StrategyConfig) -> Tuple[List[dict], StrategyConfig]:
    """
    One parameter at a time from the baseline, scored on TRAIN only.

    A coordinate sweep rather than the full product: 13 axes at ~4 values each
    is 10^7 configurations, and the best of 10^7 on 17 years of one market is a
    number about the search, not about the strategy. Moving one axis at a time
    over ~50 configurations shows which parameters the result is actually
    sensitive to, and leaves the holdout still meaning something.
    """
    rows: List[dict] = []
    for param, values in GRID.items():
        for val in values:
            cfg = replace(base, **{param: val})
            s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
            # The holdout is measured here too, in the same pass over this
            # feature set, purely so it is not rebuilt later. Nothing in the
            # selection below is allowed to read these two columns.
            fw = eng.run(cfg)
            rows.append({
                "param": param, "value": val, "cfg": cfg,
                "cagr": s["cagr"], "max_dd": s["max_dd"], "sharpe": s["sharpe"],
                "n_trades": s["n_trades"], "expectancy_r": s["expectancy_r"],
                "exposure": s["exposure"], "score": score(s),
                "is_base": getattr(base, param) == val,
                "oos1": period(fw, *OOS1)["ret"], "oos2": period(fw, *OOS2)["ret"],
            })
            b = "  <- baseline" if rows[-1]["is_base"] else ""
            print(f"  {param:<18} {val!s:>7}  CAGR {s['cagr']:6.2%}  "
                  f"DD {s['max_dd']:6.2%}  n {s['n_trades']:4d}  "
                  f"calmar {rows[-1]['score']:5.2f}{b}", flush=True)
    best = max(rows, key=lambda r: r["score"])
    return rows, best["cfg"]


def main() -> int:
    if not BARS:
        print("set PULSE_BARS (and PULSE_CONSTITUENTS)", file=sys.stderr)
        return 2

    print("── loading bars ─────────────────────────────────────────────")
    t = time.time()
    md = data.from_parquet(BARS, CONSTITUENTS)
    print(f"{md.shape[1]} symbols, {md.shape[0]} sessions, "
          f"{md.dates[0]}..{md.dates[-1]}  ({time.time() - t:.0f}s)\n")

    base = preset("balanced")
    eng = Engine(md)

    # ── 1. leakage proof ────────────────────────────────────────────────────
    # Everything below assumes a row of features depends only on rows at or
    # before it. Truncate the lake at the train boundary, recompute, and demand
    # the overlap match bit for bit. This is the one check that, if it fails,
    # invalidates the entire exercise -- so it runs first and it is fatal.
    print("── leakage check: features(truncated) vs features(full) ─────")
    cut = int(np.searchsorted(md.dates, np.datetime64(TRAIN_END)))
    md_tr = rules.MarketData(
        symbols=md.symbols, dates=md.dates[:cut],
        open=md.open[:cut], high=md.high[:cut], low=md.low[:cut],
        close=md.close[:cut], volume=md.volume[:cut], turnover=md.turnover[:cut],
        raw_close=md.raw_close[:cut], is_eq=md.is_eq[:cut], sector=md.sector,
    )
    f_full, f_tr = eng.feats(base), rules.compute_features(md_tr, base)
    bad = []
    for f in fields(rules.Features):
        a, b = getattr(f_full, f.name), getattr(f_tr, f.name)
        if a is None or b is None:
            continue
        a = a[:cut] if a.ndim and a.shape[0] == md.shape[0] else a
        if a.shape != b.shape or not np.array_equal(a, b, equal_nan=True):
            bad.append(f.name)
    if bad:
        print(f"  FAIL: {bad} differ over the overlap -- lookahead present")
        return 1
    print(f"  OK: all {len(fields(rules.Features))} feature blocks identical "
          f"over {cut} sessions to {md.dates[cut - 1]}\n")

    # ── 2. the incumbent, for reference ─────────────────────────────────────
    # `balanced` was selected against the whole lake, so its 2025-26 numbers are
    # in-sample and are shown only as the thing the honest run is compared to.
    print("── incumbent `balanced` (selected on the FULL lake) ─────────")
    full = eng.run(base)
    inc = {"train": period(full, None, TRAIN_END),
           "oos1": period(full, *OOS1), "oos2": period(full, *OOS2)}
    for k, v in inc.items():
        print(f"  {k:<6} {v['from']}..{v['to']}  ret {v['ret']:7.2%}  "
              f"CAGR {v['cagr']:7.2%}  DD {v['max_dd']:7.2%}  "
              f"expo {v['exposure']:5.1%}")
    print()

    # ── 3. select on TRAIN only ─────────────────────────────────────────────
    print(f"── coordinate sweep on TRAIN only (to {md.dates[cut - 1]}) ──")
    rows, chosen = coordinate_sweep(eng, base)
    n_cfgs = len(rows)
    print(f"\n  {n_cfgs} configurations compared on TRAIN")
    print(f"  chosen by {CRITERION} (n_trades >= {MIN_TRADES}):")
    diff = {f.name: getattr(chosen, f.name) for f in fields(StrategyConfig)
            if getattr(chosen, f.name) != getattr(base, f.name)}
    print(f"    {diff or 'identical to balanced'}\n")

    # ── 4. apply the frozen pick to the held-out years ──────────────────────
    print("── frozen train-selected config, applied forward ────────────")
    res = eng.run(chosen)
    sel = {"train": period(res, None, TRAIN_END),
           "oos1": period(res, *OOS1), "oos2": period(res, *OOS2)}
    for k, v in sel.items():
        print(f"  {k:<6} {v['from']}..{v['to']}  ret {v['ret']:7.2%}  "
              f"CAGR {v['cagr']:7.2%}  DD {v['max_dd']:7.2%}  "
              f"expo {v['exposure']:5.1%}")

    # A book actually started on 1 Jan of the holdout, rather than a slice of
    # one that had been compounding since 2008. Same rules, fresh capital --
    # this is the number that answers "what would it have made me".
    print("\n── fresh book opened at the start of each holdout period ────")
    fresh = {}
    for tag, (s0, s1) in (("oos1", OOS1), ("oos2", OOS2)):
        for label, cfg in (("balanced", base), ("train-selected", chosen)):
            r = eng.run(cfg, start=s0, end=s1)
            p = period(r, None, None)
            sm = backtest.summarise(r)
            fresh[(tag, label)] = {**p, "n_trades": sm["n_trades"],
                                   "win_rate": sm["win_rate"]}
            print(f"  {tag} {label:<15} {p['from']}..{p['to']}  "
                  f"ret {p['ret']:7.2%}  DD {p['max_dd']:7.2%}  "
                  f"{sm['n_trades']:3d} trades  win {sm['win_rate']:5.1%}  "
                  f"expo {p['exposure']:5.1%}")

    # ── 5. the whole sweep, out of sample ───────────────────────────────────
    # The distribution is the real evidence. A strategy whose edge is a fit to
    # history has a train-optimum that collapses out of sample while the rest of
    # the grid is noise; a strategy with an edge carries most of the grid across.
    print("\n── every swept config, out of sample ────────────────────────")
    o1 = np.array([r["oos1"] for r in rows])
    o2 = np.array([r["oos2"] for r in rows])
    tr = np.array([r["cagr"] for r in rows])
    for label, arr in (("2025", o1), ("2026 YTD", o2)):
        print(f"  {label:<9} median {np.median(arr):7.2%}   "
              f"p10 {np.percentile(arr, 10):7.2%}   p90 {np.percentile(arr, 90):7.2%}   "
              f"positive {np.mean(arr > 0):5.1%} of {len(arr)}")
    ok = np.isfinite(tr) & (tr > -1)
    print(f"  rank correlation, TRAIN CAGR vs 2025 return: "
          f"{np.corrcoef(_rank(tr[ok]), _rank(o1[ok]))[0, 1]:+.2f}")
    print(f"  rank correlation, TRAIN CAGR vs 2026 return: "
          f"{np.corrcoef(_rank(tr[ok]), _rank(o2[ok]))[0, 1]:+.2f}")

    out = {
        "train_end": str(md.dates[cut - 1]), "lake_end": str(md.dates[-1]),
        "n_configs": n_cfgs, "criterion": CRITERION,
        "chosen_diff": dict(diff.items()),
        "incumbent": inc, "selected": sel,
        "fresh": {f"{a}/{b}": v for (a, b), v in fresh.items()},
        "sweep": [{k: v for k, v in r.items() if k != "cfg"} for r in rows],
    }
    dest = os.path.join(os.path.dirname(BARS), "walkforward.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"\nwrote {dest}")
    return 0


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="stable")
    r = np.empty(len(a), float)
    r[order] = np.arange(len(a), dtype=float)
    return r


if __name__ == "__main__":
    raise SystemExit(main())
