"""
Does the missing 19% of sector labels actually matter?

BSE labels ~81% of the lake's equities. The rest are NSE-only listings and old
symbols with no ISIN. Filling them would mean scraping a third-party site behind
a headless browser, mapping a 37-sector vocabulary onto BSE's 22, and accepting
labels that exist only for companies still listed.

That is only worth doing if the unlabeled names are ones the book could ever buy.
The universe already demands top-500 by 60-day median turnover and >= Rs 2 crore,
and a company too small for BSE is usually too small for that too. So this counts
the gap where it actually bites, in three progressively narrower places:

    universe-days  how much of the tradeable universe is unlabeled
    candidates     how many entry signals name an unlabeled symbol
    trades         how many positions the book actually opened in one

A gap that is large in the lake and negligible in the trades is not a gap worth
paying for.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import data, rules
from pipeline.compute.strategy.config import preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    lab = np.array([bool(s) for s in md.sector]) if md.sector is not None \
        else np.zeros(len(md.symbols), bool)
    eq = md.is_equity if md.is_equity is not None else np.ones(len(md.symbols), bool)
    print(f"{len(md.symbols)} symbols · {int(eq.sum())} equities · "
          f"{int((lab & eq).sum())} labelled ({(lab & eq).sum() / max(eq.sum(), 1):.0%})")

    # Overlay off, so the universe is not itself filtered by the labels being
    # measured. The per-underlying cap stays on for the trade count.
    cfg = replace(preset("balanced"), sector_top_frac=0.0, max_per_sector=0,
                  max_per_group=1)
    eng = Engine(md)
    feats = eng.feats(cfg)

    # ── 1. universe-days ────────────────────────────────────────────────────
    days = feats.universe.sum(axis=0)
    in_uni = days > 0
    tot = days.sum()
    print("\n── the tradeable universe ──────────────────────────────────")
    print(f"  distinct symbols ever in universe : {int(in_uni.sum())}")
    print(f"    labelled   {int((in_uni & lab).sum()):5d}  "
          f"({(in_uni & lab).sum() / max(in_uni.sum(), 1):.1%})")
    print(f"    unlabelled {int((in_uni & ~lab).sum()):5d}")
    print(f"  universe-days unlabelled          : "
          f"{days[~lab].sum() / max(tot, 1):.1%} of {int(tot):,}")

    # ── 2. entry signals ────────────────────────────────────────────────────
    # What the rules would actually have proposed, over the whole lake.
    n_sig = np.zeros(len(md.symbols), np.int64)
    t0 = cfg.lookback_needed + 10
    for t in range(t0, len(md.dates)):
        for c in rules.entry_candidates(md, feats, cfg, t):
            n_sig[c.col] += 1
    sig_tot = n_sig.sum()
    print("\n── entry signals generated ─────────────────────────────────")
    print(f"  total signals                     : {int(sig_tot):,}")
    print(f"    on unlabelled symbols           : {n_sig[~lab].sum() / max(sig_tot, 1):.1%}")
    print(f"  distinct symbols signalled        : {int((n_sig > 0).sum())}, "
          f"of which unlabelled {int(((n_sig > 0) & ~lab).sum())}")

    # ── 3. positions actually opened ────────────────────────────────────────
    res = eng.run(cfg)
    col_lab = {int(i): bool(lab[i]) for i in range(len(md.symbols))}
    opened = list(res.trades)
    unl = [p for p in opened if not col_lab.get(p.col, False)]
    pnl_all = sum(p.pnl for p in opened)
    pnl_unl = sum(p.pnl for p in unl)
    print("\n── positions the book actually opened, 2008-2026 ───────────")
    print(f"  trades {len(opened)}, of which on unlabelled symbols: "
          f"{len(unl)} ({len(unl) / max(len(opened), 1):.1%})")
    print(f"  their share of closed P&L         : {pnl_unl / pnl_all if pnl_all else 0:.1%}")

    for tag, (s0, s1) in (("2025", OOS1), ("2026 YTD", OOS2)):
        a, b = np.datetime64(s0), np.datetime64(s1) if s1 else md.dates[-1]
        win = [p for p in opened if p.exit_date is not None and a <= p.exit_date <= b]
        wu = [p for p in win if not col_lab.get(p.col, False)]
        print(f"  {tag:<9} {len(win):3d} trades, {len(wu)} unlabelled "
              f"({len(wu) / max(len(win), 1):.1%})")

    tr = period(res, None, TRAIN_END)
    print(f"\n  (reference: this config trains at {tr['cagr']:.2%} / DD {tr['max_dd']:.2%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
