"""
Three questions the walk-forward left open.

  1. Is holding ~65% cash an edge, or a drag? What happens at full deployment?
  2. Was 2025 weak because the stock selection was wrong, or because too little
     capital was working?
  3. Is there a variant that is actually better -- on TRAIN *and* on the two
     held-out periods, not on one of them?

Everything is scored the same way the walk-forward scores: parameters are only
ever compared on TRAIN (to 2024-12-31), and 2025 / 2026-YTD are reported after
the fact. A variant that wins only out of sample has not been validated, it has
been lucky, and the tables below keep the two columns apart so that is visible.

    PULSE_BARS=... PULSE_CONSTITUENTS=... PULSE_INDEX=... \
    uv run python scripts/deployment_study.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import List, Optional, Tuple

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDEX = os.environ.get("PULSE_INDEX")


def pct(x: Optional[float]) -> str:
    return "     -" if x is None else f"{x:6.2%}"


# ── benchmarks ──────────────────────────────────────────────────────────────
def index_series(path: str, name: str) -> Tuple[np.ndarray, np.ndarray]:
    t = pq.read_table(path)
    cols = {c: t.column(c).to_pylist() for c in ("index_name", "date", "close")}
    keep = [i for i, n in enumerate(cols["index_name"]) if n == name]
    d = np.array([cols["date"][i] for i in keep], dtype="datetime64[D]")
    c = np.array([cols["close"][i] for i in keep], dtype=float)
    o = np.argsort(d)
    return d[o], c[o]


def buy_hold(d: np.ndarray, c: np.ndarray, start: str, end: Optional[str]) -> dict:
    lo = int(np.searchsorted(d, np.datetime64(start)))
    hi = int(np.searchsorted(d, np.datetime64(end))) if end else len(d)
    if hi - lo < 2:
        return {"ret": None, "max_dd": None}
    seg = c[lo:hi]
    dd = seg / np.maximum.accumulate(seg) - 1.0
    return {"ret": float(seg[-1] / seg[0] - 1.0), "max_dd": float(dd.min()),
            "from": str(d[lo]), "to": str(d[hi - 1])}


def timed_index(ew: np.ndarray, regime: np.ndarray, dates: np.ndarray,
                start: str, end: Optional[str], cash_yield: float = 0.05) -> dict:
    """
    Hold the equal-weight index while the regime is ON, cash otherwise.

    The regime is read from session t-1 and applied to t's return, because that
    is what the book does -- a signal on the close is acted on at the next open.
    This is the control that separates the two things the strategy claims to do:
    if this matches the book, the timing is the whole edge and the stock
    selection is decoration.
    """
    lo = int(np.searchsorted(dates, np.datetime64(start)))
    hi = int(np.searchsorted(dates, np.datetime64(end))) if end else len(dates)
    r = ew[lo:hi] / ew[lo - 1:hi - 1] - 1.0
    on = regime[lo - 1:hi - 1]
    daily = np.where(on, r, cash_yield / 252.0)
    eq = np.cumprod(1.0 + daily)
    dd = eq / np.maximum.accumulate(eq) - 1.0
    return {"ret": float(eq[-1] - 1.0), "max_dd": float(dd.min()),
            "on_frac": float(on.mean())}


# ── ladders and variants ────────────────────────────────────────────────────
def row(eng: Engine, label: str, cfg: StrategyConfig) -> dict:
    """One config, scored on TRAIN and then reported on both holdouts."""
    full = eng.run(cfg)
    tr = period(full, None, TRAIN_END)
    o1 = period(full, *OOS1)
    o2 = period(full, *OOS2)
    s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
    return {
        "label": label, "cfg": cfg,
        "expo": tr["exposure"], "train_cagr": tr["cagr"], "train_dd": tr["max_dd"],
        "train_calmar": tr["cagr"] / abs(tr["max_dd"]) if tr["max_dd"] < 0 else 0.0,
        "n_trades": s["n_trades"], "expectancy_r": s["expectancy_r"],
        "oos1": o1["ret"], "oos1_dd": o1["max_dd"], "oos1_expo": o1["exposure"],
        "oos2": o2["ret"], "oos2_dd": o2["max_dd"], "oos2_expo": o2["exposure"],
    }


HEAD = (f"  {'variant':<34} {'expo':>5} {'trCAGR':>7} {'trDD':>7} {'calmar':>6} "
        f"{'2025':>7} {'2026':>7}")


def show(r: dict) -> None:
    print(f"  {r['label']:<34} {r['expo']:5.0%} {r['train_cagr']:7.2%} "
          f"{r['train_dd']:7.2%} {r['train_calmar']:6.2f} "
          f"{r['oos1']:7.2%} {r['oos2']:7.2%}", flush=True)


def main() -> int:
    print("── loading ──────────────────────────────────────────────────")
    md = data.from_parquet(BARS, CONSTITUENTS)
    eng = Engine(md)
    base = preset("balanced")
    feats = eng.feats(base)
    print(f"{md.shape[1]} symbols, {md.dates[0]}..{md.dates[-1]}\n")

    # ── 1. what did the market actually do? ─────────────────────────────────
    # -3.46% in 2025 is only bad relative to something. Without this section the
    # holdout number cannot be read at all.
    print("── 1. benchmarks over the held-out periods ──────────────────")
    print(f"  {'series':<34} {'2025':>7} {'  DD':>7} {'2026YTD':>8} {'  DD':>7}")
    if INDEX:
        for name in ("NIFTY 50", "NIFTY MIDCAP 50", "NIFTY 500"):
            try:
                d, c = index_series(INDEX, name)
            except Exception:  # noqa: BLE001 — an absent index is not an error here
                continue
            if len(d) == 0:
                continue
            a, b = buy_hold(d, c, *OOS1), buy_hold(d, c, *OOS2)
            print(f"  {name:<34} {pct(a['ret'])} {pct(a['max_dd'])} "
                  f"{pct(b['ret']):>8} {pct(b['max_dd'])}")
    ew, reg = feats.ew_index, feats.regime
    a = buy_hold(md.dates, ew, *OOS1)
    b = buy_hold(md.dates, ew, *OOS2)
    print(f"  {'equal-weight top-200 (buy & hold)':<34} {pct(a['ret'])} "
          f"{pct(a['max_dd'])} {pct(b['ret']):>8} {pct(b['max_dd'])}")
    ta = timed_index(ew, reg, md.dates, *OOS1)
    tb = timed_index(ew, reg, md.dates, *OOS2)
    print(f"  {'  same, held only while regime ON':<34} {pct(ta['ret'])} "
          f"{pct(ta['max_dd'])} {pct(tb['ret']):>8} {pct(tb['max_dd'])}")
    print(f"  regime ON {ta['on_frac']:.0%} of 2025, {tb['on_frac']:.0%} of 2026 YTD\n")

    # ── 2. how much of the book's return is interest, not stocks? ───────────
    # At 34% average deployment two thirds of the book is a cash instrument, so
    # this is not a footnote -- it is most of the balance sheet.
    print("── 2. return split: stocks vs idle cash ─────────────────────")
    print(f"  {'cash yield':<34} {'trCAGR':>7} {'2025':>7} {'2026':>7}")
    for cy in (0.0, 0.05, 0.065):
        c = replace(base, cash_yield=cy)
        r = row(eng, f"{cy:.1%}", c)
        print(f"  {'  ' + f'{cy:.1%} on idle balance':<32} {r['train_cagr']:7.2%} "
              f"{r['oos1']:7.2%} {r['oos2']:7.2%}")
    print()

    # ── 3. the deployment ladder ────────────────────────────────────────────
    # Family A keeps ATR risk sizing and turns the dial up. Family B abandons
    # volatility targeting: risk_pct is set high enough that `max_weight` always
    # binds, so every position is the same fraction of equity and the book is
    # fully invested whenever it holds its full slate.
    print("── 3a. deployment via risk per trade (ATR-sized) ────────────")
    print(HEAD)
    ladder_a = []
    for rp in (0.0040, 0.0060, 0.0080, 0.0120, 0.0160, 0.0200):
        r = row(eng, f"risk_pct {rp:.2%}", replace(base, risk_pct=rp))
        ladder_a.append(r)
        show(r)

    print("\n── 3b. equal weight, fully deployed while regime ON ─────────")
    print(HEAD)
    ladder_b = []
    for n in (5, 8, 10, 12, 15, 20):
        cfg = replace(base, max_positions=n, risk_pct=0.50, max_weight=1.0 / n)
        r = row(eng, f"equal-weight, {n} names", cfg)
        ladder_b.append(r)
        show(r)

    print("\n── 3c. partial equal weight (cash buffer retained) ──────────")
    print(HEAD)
    for frac in (0.50, 0.70, 0.85):
        n = 12
        cfg = replace(base, max_positions=n, risk_pct=0.50, max_weight=frac / n)
        show(row(eng, f"equal-weight 12 names, {frac:.0%} invested", cfg))

    # ── 4. alternative rule sets ────────────────────────────────────────────
    print("\n── 4. alternative rule sets (each judged on TRAIN first) ────")
    print(HEAD)
    # Ordered so that configurations sharing a feature set are adjacent: the
    # cache holds two, and a rebuild costs 150s against a 0.6s backtest.
    variants: List[Tuple[str, StrategyConfig]] = [
        # -- share the baseline feature set --
        ("balanced (incumbent)", base),
        ("no regime exit (entries only)", replace(base, regime_exit=False)),
        ("wider stop 4 ATR", replace(base, stop_atr=4.0)),
        ("long time stop 120d", replace(base, time_stop=120)),
        ("no time stop", replace(base, time_stop=10**6)),
        ("looser RS (top 30%)", replace(base, rs_min_pct=0.70)),
        # -- each of these needs its own feature set --
        ("no sector overlay", replace(base, sector_top_frac=0.0)),
        ("faster regime (50d)", replace(base, regime_ma=50)),
        ("slower regime (150d)", replace(base, regime_ma=150)),
        ("slower regime (200d)", replace(base, regime_ma=200)),
        ("12m momentum rank", replace(base, rs_lookback=252)),
        ("3m momentum rank", replace(base, rs_lookback=63)),
        ("all-time-high breakout", replace(base, breakout_lookback=0)),
        ("50d EMA exit", replace(base, ema_exit=50, time_stop=10**6)),
    ]
    rows = []
    for label, cfg in variants:
        r = row(eng, label, cfg)
        rows.append(r)
        show(r)

    # ── 5. the combination worth trying ─────────────────────────────────────
    # Anything below is chosen on TRAIN only; the holdout columns are the test.
    print("\n── 5. best TRAIN combinations, carried forward ──────────────")
    print(HEAD)
    combos = []
    for ts, ema in ((60, None), (10**6, 50)):
        for n, frac in ((12, 1.0), (15, 1.0), (20, 1.0)):
            cfg = replace(base, max_positions=n, risk_pct=0.50,
                          max_weight=frac / n, time_stop=ts, ema_exit=ema)
            lbl = f"EW {n} names, {'EMA50 exit' if ema else '60d stop'}"
            r = row(eng, lbl, cfg)
            combos.append(r)
            show(r)

    print("\n── summary: does anything beat the incumbent on all three? ──")
    inc = rows[0]
    print(f"  incumbent: train {inc['train_cagr']:.2%} / DD {inc['train_dd']:.2%}, "
          f"2025 {inc['oos1']:.2%}, 2026 {inc['oos2']:.2%}")
    allr = ladder_a + ladder_b + rows + combos
    better = [r for r in allr
              if r["train_cagr"] > inc["train_cagr"]
              and r["oos1"] > inc["oos1"] and r["oos2"] > inc["oos2"]]
    if not better:
        print("  nothing dominates on all three periods.")
    for r in sorted(better, key=lambda x: -x["train_calmar"]):
        print(f"  DOMINATES: {r['label']:<34} train {r['train_cagr']:6.2%} "
              f"DD {r['train_dd']:6.2%}  2025 {r['oos1']:6.2%}  2026 {r['oos2']:6.2%}")

    import json
    dest = os.path.join(os.path.dirname(BARS), "deployment.json")
    with open(dest, "w") as fh:
        json.dump([{k: v for k, v in r.items() if k != "cfg"} for r in allr],
                  fh, indent=1, default=str)
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
