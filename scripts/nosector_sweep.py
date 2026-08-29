"""
Re-tune on the uncontaminated base.

Every parameter in `balanced` was chosen with the sector overlay switched on --
that is, around a filter that silently restricts the book to companies known to
be in the NIFTY 500 in 2026. If the overlay goes, the tuning around it is no
longer the tuning that was validated, so this re-runs the same coordinate sweep
with `sector_top_frac=0` as the starting point.

Selection is on TRAIN (to 2024-12-31) alone, by the same criterion the
walk-forward used. The 2025 and 2026 columns are printed but never consulted.
"""
from __future__ import annotations

import os
import sys
from dataclasses import fields, replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import GRID, OOS1, OOS2, TRAIN_END, Engine, period, score

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS)
    eng = Engine(md)
    incumbent = preset("balanced")
    # The uncontaminated starting point: no sector selection, and no per-sector
    # cap either, since that cap reads the same present-day labels.
    base = replace(incumbent, sector_top_frac=0.0, max_per_sector=0)

    def line(label, cfg):
        full = eng.run(cfg)
        tr = period(full, None, TRAIN_END)
        s = backtest.summarise(eng.run(cfg, end=TRAIN_END))
        o1, o2 = period(full, *OOS1)["ret"], period(full, *OOS2)["ret"]
        cal = tr["cagr"] / abs(tr["max_dd"]) if tr["max_dd"] < 0 else 0.0
        print(f"  {label:<30} {tr['exposure']:5.0%} {tr['cagr']:7.2%} "
              f"{tr['max_dd']:7.2%} {cal:6.2f} {s['n_trades']:5d} "
              f"{o1:8.2%} {o2:8.2%}", flush=True)
        return {"label": label, "cfg": cfg, "cagr": tr["cagr"],
                "max_dd": tr["max_dd"], "calmar": cal, "n": s["n_trades"],
                "score": score(s), "oos1": o1, "oos2": o2}

    head = (f"  {'variant':<30} {'expo':>5} {'trCAGR':>7} {'trDD':>7} "
            f"{'calmar':>6} {'n':>5} {'2025':>8} {'2026':>8}")
    print("── starting points ─────────────────────────────────────────")
    print(head)
    line("balanced (incumbent)", incumbent)
    line("no overlay, keep sector cap", replace(incumbent, sector_top_frac=0.0))
    base_row = line("no overlay, no sector cap", base)

    print("\n── coordinate sweep on TRAIN, from the clean base ──────────")
    print(head)
    rows = [base_row]
    axes = {k: v for k, v in GRID.items() if k not in ("sector_top_frac",)}
    for param, values in axes.items():
        for val in values:
            if getattr(base, param) == val:
                continue
            rows.append(line(f"{param}={val}", replace(base, **{param: val})))

    print("\n── deployment ladder on the clean base ─────────────────────")
    print(head)
    for n in (8, 10, 12, 15, 20):
        rows.append(line(f"equal-weight {n} names",
                         replace(base, max_positions=n, risk_pct=0.50,
                                 max_weight=1.0 / n)))
    for frac in (0.50, 0.70):
        rows.append(line(f"equal-weight 12, {frac:.0%} invested",
                         replace(base, max_positions=12, risk_pct=0.50,
                                 max_weight=frac / 12)))

    # ── selection, on TRAIN only ────────────────────────────────────────────
    best = max(rows, key=lambda r: r["score"])
    print(f"\n── best on TRAIN by calmar: {best['label']} ────────────────")
    diff = {f.name: getattr(best["cfg"], f.name) for f in fields(StrategyConfig)
            if getattr(best["cfg"], f.name) != getattr(incumbent, f.name)}
    print(f"  differs from balanced by: {diff}")
    print(f"  TRAIN {best['cagr']:.2%} / DD {best['max_dd']:.2%} / calmar {best['calmar']:.2f}")
    print(f"  held out: 2025 {best['oos1']:+.2%}   2026 YTD {best['oos2']:+.2%}")

    print("\n── holdout distribution across the clean sweep ─────────────")
    for tag, key in (("2025", "oos1"), ("2026 YTD", "oos2")):
        a = np.array([r[key] for r in rows])
        print(f"  {tag:<9} median {np.median(a):7.2%}  p10 {np.percentile(a, 10):7.2%}  "
              f"p90 {np.percentile(a, 90):7.2%}  positive {np.mean(a > 0):5.1%} of {len(a)}")

    import json
    dest = os.path.join(os.path.dirname(BARS), "nosector.json")
    with open(dest, "w") as fh:
        json.dump([{k: v for k, v in r.items() if k != "cfg"} for r in rows],
                  fh, indent=1, default=str)
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
