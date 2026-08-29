"""
The whole strategy, re-derived from scratch on corrected labels.

Everything earlier in this branch was diagnosis. This is the rebuild: structure
chosen on 2008-2024 alone, against a stated drawdown budget, then applied
untouched to 2025 and 2026. Nothing below reads the holdout before the choice is
frozen, and the selection rule is fixed here rather than after the fact.

    budget      max drawdown no worse than -11% over TRAIN
    objective   highest CAGR inside that budget
    floor       at least 300 closed trades, or the sample is not evidence

The ETF sleeve is reported separately throughout. Sector ETFs were not liquid in
India before roughly 2023 — six to nine fund units cleared the turnover filter
before 2018, against 135 today — so a single blended 18-year figure would imply
evidence that does not exist. What can be said about the sleeve is said about
the years it could actually have traded.
"""
from __future__ import annotations

import os
import sys
from dataclasses import fields, replace
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

DD_BUDGET = -0.11
MIN_TRADES = 300


def stats(eng: Engine, cfg: StrategyConfig, end: Optional[str]) -> dict:
    res = eng.run(cfg, end=end)
    s = backtest.summarise(res)
    s["_res"] = res
    return s


def row(label: str, s: dict) -> str:
    return (f"  {label:<32} {s['cagr']:7.2%} {s['max_dd']:7.2%} "
            f"{s['cagr'] / abs(s['max_dd']) if s['max_dd'] < 0 else 0:6.2f} "
            f"{s['sharpe']:6.2f} {s['n_trades']:5d} {s['win_rate']:6.1%} "
            f"{s['expectancy_r']:+6.2f} {s['exposure']:6.1%}")


HEAD = (f"  {'variant':<32} {'CAGR':>7} {'maxDD':>7} {'calmar':>6} {'sharpe':>6} "
        f"{'trades':>5} {'win':>6} {'expR':>6} {'expo':>6}")


def etf_share(res, md) -> Tuple[int, float]:
    """Trades and P&L share attributable to fund units."""
    fund = {i for i in range(len(md.symbols))
            if md.is_equity is not None and not md.is_equity[i]}
    tr = [p for p in res.trades if p.col in fund]
    tot = sum(p.pnl for p in res.trades)
    return len(tr), (sum(p.pnl for p in tr) / tot if tot else 0.0)


def main() -> int:
    md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
    eng = Engine(md)
    base = preset("balanced")
    # The duplicate-underlying cap is on everywhere: without it one commodity
    # bet held through six wrappers decides which structure looks best.
    core = replace(base, max_per_group=1, require_sector_label=False)

    print("── 1. structure, scored on TRAIN 2008-01..2024-12 only ─────")
    print(HEAD)
    structures: List[Tuple[str, StrategyConfig]] = [
        ("stocks, overlay on", core),
        ("stocks, overlay off", replace(core, sector_top_frac=0.0, max_per_sector=0)),
        ("stocks, equities only, overlay on", replace(core, equity_only=True)),
        ("stocks + ETF sleeve, overlay on", replace(core, etf_as_sector=True)),
        ("stocks + ETF sleeve, top 50%", replace(core, etf_as_sector=True,
                                                 sector_top_frac=0.50)),
    ]
    scored = []
    for label, cfg in structures:
        s = stats(eng, cfg, TRAIN_END)
        scored.append((label, cfg, s))
        print(row(label, s), flush=True)

    # ── 2. risk ladder on each structure, to meet the drawdown budget ───────
    # Risk per trade is the only dial turned here. It moves return and drawdown
    # together and was shown to be the dominant lever, so meeting a drawdown
    # budget is a sizing question rather than a rule question.
    print(f"\n── 2. sizing to a {DD_BUDGET:.0%} drawdown budget, TRAIN only ──")
    print(HEAD)
    best = None
    for label, cfg, _ in scored:
        for rp in (0.0030, 0.0035, 0.0040, 0.0045, 0.0050, 0.0060):
            c = replace(cfg, risk_pct=rp)
            s = stats(eng, c, TRAIN_END)
            ok = s["max_dd"] >= DD_BUDGET and s["n_trades"] >= MIN_TRADES
            mark = "  <- inside budget" if ok else ""
            print(row(f"{label[:20]} r={rp:.2%}", s) + mark, flush=True)
            if ok and (best is None or s["cagr"] > best[2]["cagr"]):
                best = (label, c, s)

    if best is None:
        print("\n  nothing met the budget; widen it or accept a smaller book")
        return 1

    label, chosen, train = best
    print(f"\n── 3. chosen on TRAIN: {label}, risk {chosen.risk_pct:.2%} ─────")
    diff = {f.name: getattr(chosen, f.name) for f in fields(StrategyConfig)
            if getattr(chosen, f.name) != getattr(base, f.name)}
    print(f"  differs from the shipped preset by: {diff}")
    for k in ("cagr", "max_dd", "sharpe", "n_trades", "win_rate", "payoff",
              "profit_factor", "expectancy_r", "median_hold", "exposure"):
        print(f"    {k:<14} {train[k]:.4f}" if isinstance(train[k], float)
              else f"    {k:<14} {train[k]}")

    # ── 4. the held-out years, untouched ────────────────────────────────────
    print("\n── 4. applied forward, never seen during selection ─────────")
    full = eng.run(chosen)
    print(f"  {'period':<16} {'return':>8} {'maxDD':>8} {'expo':>7} {'trades':>7}")
    for tag, (s0, s1) in (("TRAIN", (None, TRAIN_END)),
                          ("2025", OOS1), ("2026 YTD", OOS2)):
        p = period(full, s0, s1)
        print(f"  {tag:<16} {p['ret']:8.2%} {p['max_dd']:8.2%} "
              f"{p['exposure']:7.1%} {p['n_closed']:7d}")

    n_etf, pnl_etf = etf_share(full, md)
    print(f"\n  fund units: {n_etf} of {len(full.trades)} trades, "
          f"{pnl_etf:.1%} of closed P&L")

    print("\n── 5. year by year ─────────────────────────────────────────")
    print(f"  {'year':<6} {'return':>9} {'worst DD':>10}")
    for y, r, _eq, dd in backtest.by_year(full):
        flag = "   <- held out" if y >= 2025 else ""
        print(f"  {y:<6} {r:9.2%} {dd:10.2%}{flag}")

    print("\n── 6. full-period summary on the frozen config ─────────────")
    s = backtest.summarise(full)
    for k in ("years", "cagr", "max_dd", "sharpe", "n_trades", "win_rate",
              "payoff", "profit_factor", "expectancy_r", "median_hold",
              "exposure", "end"):
        v = s[k]
        print(f"  {k:<14} {v:.4f}" if isinstance(v, float) else f"  {k:<14} {v}")
    print(f"  skipped        {s['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
