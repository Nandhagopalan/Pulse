"""
Six changes, tried one axis at a time, then combined.

Each is a request with a clear intent, and each is measured against the same
budget: TRAIN drawdown no worse than -15%, the stated upper limit. Nothing here
reads 2025 or 2026 until a config is frozen.

  cash        idle balance earns nothing, and deployment goes as high as it can
  ramp        size scales in over a recovery rather than all at once
  confirm     the regime must stay off for a week before the book is closed
  weekly      a weekly close below a weekly EMA trails the position out
  patience    the 60-session time stop lengthened, or removed entirely
  risk        what more risk actually buys

The first is not free and is reported as such: the book sits ~75% in cash, so
giving up the 5% assumption removes roughly a third of the historical return
before any change earns it back.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.compute.strategy import backtest, data
from pipeline.compute.strategy.config import StrategyConfig, preset

from scripts.walkforward import OOS1, OOS2, TRAIN_END, Engine, period

BARS = os.environ["PULSE_BARS"]
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")
INDUSTRY = os.environ.get("PULSE_INDUSTRY")

DD_CAP = -0.15
MIN_TRADES = 300

HEAD = (f"  {'variant':<38} {'CAGR':>7} {'maxDD':>7} {'calmar':>6} {'expo':>6} "
        f"{'trades':>6} {'win':>6} {'expR':>6}")


class Lab:
    def __init__(self) -> None:
        md = data.from_parquet(BARS, CONSTITUENTS, INDUSTRY)
        self.md = md
        self.eng = Engine(md)
        self.rows: List[dict] = []

    def train(self, label: str, cfg: StrategyConfig, keep: bool = True) -> dict:
        s = backtest.summarise(self.eng.run(cfg, end=TRAIN_END))
        r = {"label": label, "cfg": cfg, "cagr": s["cagr"], "dd": s["max_dd"],
             "calmar": s["cagr"] / abs(s["max_dd"]) if s["max_dd"] < 0 else 0.0,
             "expo": s["exposure"], "n": s["n_trades"], "win": s["win_rate"],
             "expr": s["expectancy_r"]}
        print(f"  {label:<38} {r['cagr']:7.2%} {r['dd']:7.2%} {r['calmar']:6.2f} "
              f"{r['expo']:6.1%} {r['n']:6d} {r['win']:6.1%} {r['expr']:+6.2f}",
              flush=True)
        if keep:
            self.rows.append(r)
        return r


def main() -> int:
    lab = Lab()
    base = preset("balanced")
    # The v2 book, plus the operator's stated preference: idle cash earns
    # nothing. Everything below is measured against this, not against the
    # 5%-on-cash figures quoted earlier.
    v2 = replace(base, sector_top_frac=0.0, max_per_sector=0, max_per_group=1,
                 require_sector_label=False, risk_pct=0.0040)
    nocash = replace(v2, cash_yield=0.0)

    print("── 0. what giving up the cash yield costs ──────────────────")
    print(HEAD)
    lab.train("v2, 5% on idle cash", v2, keep=False)
    lab.train("v2, 0% on idle cash", nocash, keep=False)

    print("\n── 1. deployment: equal weight, fully invested when ON ─────")
    print(HEAD)
    for n in (8, 10, 12, 15, 20):
        lab.train(f"equal weight, {n} names, 0% cash",
                  replace(nocash, max_positions=n, risk_pct=0.50, max_weight=1.0 / n))
    for frac in (0.5, 0.7, 0.85):
        lab.train(f"equal weight 12, {frac:.0%} invested",
                  replace(nocash, max_positions=12, risk_pct=0.50, max_weight=frac / 12))

    print("\n── 2. ramping in over a recovery ───────────────────────────")
    print(HEAD)
    ew12 = replace(nocash, max_positions=12, risk_pct=0.50, max_weight=1.0 / 12)
    for ramp in (0, 10, 20, 40, 60):
        lab.train(f"equal weight 12, ramp {ramp}d", replace(ew12, ramp_sessions=ramp))

    print("\n── 3. regime exit: confirm before liquidating ──────────────")
    print(HEAD)
    for c in (0, 2, 3, 5, 8, 10):
        lab.train(f"equal weight 12, confirm {c}d", replace(ew12, regime_exit_confirm=c))
        lab.train(f"risk-sized, confirm {c}d", replace(nocash, regime_exit_confirm=c))

    print("\n── 4. patience: longer holds, and a weekly trailing stop ───")
    print(HEAD)
    for ts in (60, 120, 250, 10**6):
        tag = "none" if ts > 1000 else f"{ts}d"
        lab.train(f"equal weight 12, time stop {tag}", replace(ew12, time_stop=ts))
    for span in (20, 100):
        for ts in (60, 10**6):
            tag = "none" if ts > 1000 else f"{ts}d"
            lab.train(f"weekly EMA{span} trail, time stop {tag}",
                      replace(ew12, weekly_ema_exit=span, time_stop=ts))
            lab.train(f"weekly EMA{span} + confirm 5d, ts {tag}",
                      replace(ew12, weekly_ema_exit=span, time_stop=ts,
                              regime_exit_confirm=5))

    # ── 5. combine what worked, then size to the budget ─────────────────────
    print("\n── 5. best single axes combined, then a risk ladder ────────")
    print(HEAD)
    inside = [r for r in lab.rows if r["dd"] >= DD_CAP and r["n"] >= MIN_TRADES]
    top = sorted(inside, key=lambda r: -r["cagr"])[:4]
    print(f"  ({len(inside)} of {len(lab.rows)} variants inside the {DD_CAP:.0%} cap)")
    finals: List[Tuple[str, StrategyConfig, dict]] = []
    for r in top:
        for w in (0.5, 0.7, 0.85, 1.0):
            cfg = replace(r["cfg"], max_weight=w / r["cfg"].max_positions) \
                if r["cfg"].risk_pct > 0.1 else replace(r["cfg"],
                                                        risk_pct=r["cfg"].risk_pct * w * 2)
            got = lab.train(f"{r['label'][:26]} @ {w:.0%}", cfg, keep=False)
            if got["dd"] >= DD_CAP and got["n"] >= MIN_TRADES:
                finals.append((got["label"], cfg, got))

    if not finals:
        print("\n  nothing inside the cap")
        return 1
    label, chosen, tr = max(finals, key=lambda x: x[2]["cagr"])

    print(f"\n── 6. frozen on TRAIN: {label} ──────────────")
    print(f"  TRAIN  CAGR {tr['cagr']:.2%}  DD {tr['dd']:.2%}  calmar {tr['calmar']:.2f}  "
          f"expo {tr['expo']:.1%}  {tr['n']} trades  win {tr['win']:.1%}  "
          f"expR {tr['expr']:+.2f}")
    full = lab.eng.run(chosen)
    print(f"\n  {'period':<14} {'return':>9} {'maxDD':>8} {'expo':>7}")
    for tag, (s0, s1) in (("TRAIN", (None, TRAIN_END)), ("2025", OOS1), ("2026 YTD", OOS2)):
        p = period(full, s0, s1)
        print(f"  {tag:<14} {p['ret']:9.2%} {p['max_dd']:8.2%} {p['exposure']:7.1%}")
    s = backtest.summarise(full)
    print("\n  full period:", {k: round(s[k], 4) for k in
          ("cagr", "max_dd", "sharpe", "win_rate", "payoff", "profit_factor",
           "expectancy_r", "median_hold", "exposure")})
    print("  trades:", s["n_trades"], " end:", f"{s['end']:,.0f}")
    print("\n  year by year")
    for y, r, _e, dd in backtest.by_year(full):
        flag = "  <- held out" if y >= 2025 else ""
        print(f"    {y}  {r:8.2%}  worst {dd:7.2%}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
