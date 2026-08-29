"""
The book against the indices a swing trader would otherwise have bought.

A CAGR means nothing on its own. 10% is excellent against a flat market and
poor against one that doubled, so the only honest way to read the strategy's
record is beside the thing it is competing with: buying NIFTY 50, or the
midcap index, or the smallcap index, and sitting still.

Two facts constrain what this can compare:

  * **The index series starts in 2013.** NSE's `ind_close_all` archive 404s
    before that (checked, not assumed), so 2008-2012 has bars but no index.
    Every head-to-head number below is therefore computed over 2013 onward,
    and the strategy is re-scored on that same window rather than quoting its
    full-record CAGR against a shorter index one.
  * **The indices are price return.** They exclude dividends, roughly 1-1.5%
    a year for Indian large caps, so the index columns are a slight understate.
    Pulling the other way, the book earns `cash_yield` on an idle balance that
    averages ~80% of capital, which is a large part of its return and is not
    an equity edge at all -- so the book is also run at 0% cash to show what
    the stock selection alone produced.

Index continuity is handled by splicing NSE's own renames, not by switching
index families:

    NIFTY 50        CNX NIFTY -> NIFTY 50                        (Nov 2015)
    NIFTY MIDCAP    CNX MIDCAP -> NIFTY MIDCAP 100
                      -> NIFTY FREE FLOAT MIDCAP 100             (Apr 2016)
                      -> NIFTY MIDCAP 100                        (Apr 2018)
    NIFTY SMALLCAP  the same three-step rename

The segments partition the calendar with no overlapping days, and each join is
level-continuous against an independent index on the same session, so these are
one series under successive labels rather than a basket change.

    PULSE_BARS=... PULSE_INDEX=... uv run python scripts/benchmarks.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, List, Tuple

import duckdb
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import preset

from scripts.walkforward import Engine

BARS = os.environ["PULSE_BARS"]
INDEX = os.environ["PULSE_INDEX"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

# The published book: risk 0.25%, weekly EMA20 trail, no time stop, no overlay.
RISK = 0.0025
COMMON = os.environ.get("PULSE_COMMON", "2013-02-08")   # first session all four share

# Each benchmark as the ordered list of labels NSE published it under.
CHAINS: Dict[str, List[str]] = {
    "NIFTY 50": ["CNX NIFTY", "NIFTY 50"],
    "NIFTY MIDCAP 100": ["CNX MIDCAP", "NIFTY MIDCAP 100",
                         "NIFTY FREE FLOAT MIDCAP 100"],
    "NIFTY SMALLCAP 100": ["CNX SMALLCAP", "NIFTY SMALLCAP 100",
                           "NIFTY FREE FLOAT SMALLCAP 100"],
    "NIFTY 500": ["CNX 500", "NIFTY 500"],
}


def load_chain(con, labels: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    """
    One continuous close series from a list of labels for the same index.

    Duplicate sessions would mean the labels are different baskets rather than
    successive names for one, which would make the splice invalid -- so this
    refuses rather than silently picking a side.
    """
    ph = ",".join("?" for _ in labels)
    rows = con.execute(
        f"SELECT date, close, index_name FROM read_parquet('{INDEX}') "
        f"WHERE index_name IN ({ph}) AND close > 0 ORDER BY date",
        labels,
    ).fetchall()
    dates = np.array([r[0] for r in rows], dtype="datetime64[D]")
    dup = np.flatnonzero(dates[1:] == dates[:-1])
    if dup.size:
        raise ValueError(f"{labels}: {dup.size} overlapping sessions, not a rename")
    return dates, np.array([r[1] for r in rows], dtype=float)


def curve_stats(dates: np.ndarray, level: np.ndarray) -> dict:
    """CAGR, drawdown and Sharpe of a price series, on daily returns."""
    years = (dates[-1] - dates[0]).astype("timedelta64[D]").astype(float) / 365.25
    dd = level / np.maximum.accumulate(level) - 1.0
    ret = np.diff(level) / level[:-1]
    total = level[-1] / level[0] - 1.0
    sd = ret.std()
    return {
        "cagr": (1.0 + total) ** (1.0 / years) - 1.0,
        "max_dd": float(dd.min()),
        "sharpe": float(ret.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
        "total": total,
        "years": years,
    }


def by_year(dates: np.ndarray, level: np.ndarray) -> Dict[int, Tuple[float, float]]:
    """
    Per calendar year: return, and the worst drawdown inside that year.

    The year's return is measured from the previous year's closing level, so it
    is what a holder actually earned, not a first-to-last-session move that
    would drop the new-year gap.
    """
    yrs = dates.astype("datetime64[Y]").astype(int) + 1970
    out: Dict[int, Tuple[float, float]] = {}
    for y in np.unique(yrs):
        idx = np.flatnonzero(yrs == y)
        e = level[idx]
        prev = level[idx[0] - 1] if idx[0] > 0 else e[0]
        dd = e / np.maximum.accumulate(e) - 1.0
        out[int(y)] = (float(e[-1] / prev - 1.0), float(dd.min()))
    return out


def slice_from(dates: np.ndarray, level: np.ndarray, start: str):
    lo = int(np.searchsorted(dates, np.datetime64(start)))
    return dates[lo:], level[lo:]


def main() -> int:
    con = duckdb.connect()
    bench: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for name, labels in CHAINS.items():
        bench[name] = load_chain(con, labels)
        d, _ = bench[name]
        print(f"  {name:<20} {d[0]} .. {d[-1]}  {len(d)} sessions", flush=True)

    print("\n── the book ────────────────────────────────────────────────")
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    cfg = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                  max_per_group=1, require_sector_label=False,
                  weekly_ema_exit=20, time_stop=10 ** 6, risk_pct=RISK)
    res = eng.run(cfg)
    res0 = eng.run(replace(cfg, cash_yield=0.0))

    books = {
        "Pulse (5% cash)": (res.dates.astype("datetime64[D]"), res.equity),
        "Pulse (0% cash)": (res0.dates.astype("datetime64[D]"), res0.equity),
    }
    np.savez(os.path.join(os.path.dirname(INDEX), "book_curve.npz"),
             dates=res.dates.astype("datetime64[D]"), eq5=res.equity, eq0=res0.equity)
    s = backtest.summarise(res)
    print(f"  full record  CAGR {s['cagr']:.2%}  DD {s['max_dd']:.2%}  "
          f"Sharpe {s['sharpe']:.2f}  exposure {s['exposure']:.1%}")

    # ── year by year ────────────────────────────────────────────────────────
    series = {**books, **bench}
    yb = {k: by_year(d, lv) for k, (d, lv) in series.items()}
    names = list(series)
    print("\n── calendar year returns ───────────────────────────────────")
    print("  year " + "".join(f"{n:>20}" for n in names))
    for y in range(2008, 2027):
        cells = ""
        for n in names:
            cells += f"{yb[n][y][0]:>19.2%} " if y in yb[n] else f"{'--':>20}"
        print(f"  {y} {cells}")

    print("\n── worst drawdown inside each year ─────────────────────────")
    print("  year " + "".join(f"{n:>20}" for n in names))
    for y in range(2008, 2027):
        cells = ""
        for n in names:
            cells += f"{yb[n][y][1]:>19.2%} " if y in yb[n] else f"{'--':>20}"
        print(f"  {y} {cells}")

    # ── head to head on the common window ───────────────────────────────────
    print(f"\n── {COMMON} to date, the window all four share ─────────────")
    print(f"  {'series':<20} {'CAGR':>8} {'total':>10} {'maxDD':>8} "
          f"{'Sharpe':>7} {'Calmar':>7}")
    for n in names:
        d, lv = slice_from(*series[n], COMMON)
        st = curve_stats(d, lv)
        cal = st["cagr"] / abs(st["max_dd"]) if st["max_dd"] < 0 else 0.0
        print(f"  {n:<20} {st['cagr']:>8.2%} {st['total']:>10.1%} "
              f"{st['max_dd']:>8.2%} {st['sharpe']:>7.2f} {cal:>7.2f}")

    # ── behaviour in the index's worst years ────────────────────────────────
    print("\n── the years the indices lost money ────────────────────────")
    print(f"  {'year':<6} {'NIFTY 50':>10} {'MIDCAP':>10} {'SMALLCAP':>10} "
          f"{'Pulse':>10}")
    for y in sorted(yb["NIFTY 50"]):
        n50 = yb["NIFTY 50"][y][0]
        mid = yb["NIFTY MIDCAP 100"].get(y, (0.0, 0.0))[0]
        sml = yb["NIFTY SMALLCAP 100"].get(y, (0.0, 0.0))[0]
        if min(n50, mid, sml) >= 0:
            continue
        print(f"  {y:<6} {n50:>10.2%} {mid:>10.2%} {sml:>10.2%} "
              f"{yb['Pulse (5% cash)'][y][0]:>10.2%}")

    # ── correlation to the market ───────────────────────────────────────────
    print("\n── daily correlation and beta to NIFTY 50, 2013 on ─────────")
    bd, bl = slice_from(*bench["NIFTY 50"], COMMON)
    for n in books:
        d, lv = slice_from(*series[n], COMMON)
        common = np.intersect1d(bd, d)
        bi = np.searchsorted(bd, common)
        si = np.searchsorted(d, common)
        rb = np.diff(bl[bi]) / bl[bi][:-1]
        rs = np.diff(lv[si]) / lv[si][:-1]
        beta = float(np.cov(rs, rb)[0, 1] / np.var(rb))
        print(f"  {n:<20} corr {np.corrcoef(rs, rb)[0, 1]:>6.2f}   beta {beta:>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
