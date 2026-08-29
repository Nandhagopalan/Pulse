"""
Does the sector overlay earn its place once the labels are honest?

The overlay was recommended for removal because its labels existed only for
current NIFTY 500 members: 500 of 3,967 equities, none of them delisted. A rule
that excludes what it cannot classify then reduces to "hold only today's index
members" -- a look-ahead across the training years and an unintended large-cap
screen live.

`industry.parquet` removes that specific defect. It labels ~81% of the lake's
equities and the same ~81% of the delisted ones, so the remaining gaps do not
correlate with survival. This re-runs the comparison on both label sets, so the
overlay is judged on labels that could actually have been known.

Each label set is loaded, measured and released before the next, because two
copies of the bars plus their feature sets do not fit in memory here.
"""
from __future__ import annotations

import gc
import os
import sys
from dataclasses import replace
from typing import List, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

HEAD = (f"  {'variant':<34} {'expo':>5} {'trCAGR':>7} {'trDD':>7} {'calmar':>6} "
        f"{'2025':>8} {'2026':>8}")


def coverage(md) -> str:
    if md.sector is None:
        return "no labels"
    eq = md.is_equity if md.is_equity is not None else np.ones(len(md.symbols), bool)
    lab = np.array([bool(s) for s in md.sector])
    last = np.array([md.dates[np.flatnonzero(np.isfinite(md.close[:, j]))[-1]]
                     if np.isfinite(md.close[:, j]).any() else md.dates[0]
                     for j in range(len(md.symbols))])
    live = last >= np.datetime64("2026-07-01")
    n_lab = int((lab & eq).sum())
    return (f"{n_lab}/{int(eq.sum())} equities labelled "
            f"({n_lab / max(int(eq.sum()), 1):.0%})  ·  "
            f"live {int((lab & eq & live).sum())}/{int((eq & live).sum())}  ·  "
            f"delisted {int((lab & eq & ~live).sum())}/{int((eq & ~live).sum())}  ·  "
            f"{len({str(s) for s in md.sector if s})} distinct sectors")


def run_set(label: str, md, variants: List[Tuple[str, StrategyConfig]]) -> None:
    print(f"\n── {label} ─────────────────────────────────────────")
    print(f"  {coverage(md)}")
    print(HEAD)
    eng = Engine(md)
    for name, cfg in variants:
        full = eng.run(cfg)
        tr = period(full, None, TRAIN_END)
        o1, o2 = period(full, *OOS1), period(full, *OOS2)
        cal = tr["cagr"] / abs(tr["max_dd"]) if tr["max_dd"] < 0 else 0.0
        print(f"  {name:<34} {tr['exposure']:5.0%} {tr['cagr']:7.2%} "
              f"{tr['max_dd']:7.2%} {cal:6.2f} {o1['ret']:8.2%} {o2['ret']:8.2%}",
              flush=True)


def main() -> int:
    base = preset("balanced")
    # `max_per_group` is on throughout: the duplicate-underlying defect is
    # orthogonal to the overlay question, and leaving it uncapped would let one
    # silver bet held six times decide which row looks best.
    guarded = replace(base, max_per_group=1)
    # `require_sector_label=True` is the rule as it stands: a name with no label
    # is excluded. That is what made the overlay a coverage filter, and with
    # labels now covering 96% of listed names against 45% of delisted ones it is
    # also what would screen the backtest toward survivors. Both semantics are
    # measured so the effect of the rule is separable from the effect of the data.
    variants = [
        ("overlay on, unlabelled EXCLUDED", guarded),
        ("overlay on, unlabelled PASS", replace(guarded, require_sector_label=False)),
        ("overlay top 50%, unlabelled PASS",
         replace(guarded, sector_top_frac=0.50, require_sector_label=False)),
        ("overlay off", replace(guarded, sector_top_frac=0.0, max_per_sector=0)),
    ]

    md = data.from_parquet(BARS, CONSTITUENTS)
    run_set("NIFTY 500 labels — what the book uses today", md, variants)
    del md
    gc.collect()

    if not INDUSTRY:
        print("\nset PULSE_INDUSTRY to compare against the corrected labels")
        return 0
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    run_set("BSE labels, keyed by ISIN — corrected", md, variants)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
