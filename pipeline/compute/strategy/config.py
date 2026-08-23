"""
Every tunable in the strategy, in one place.

Nothing downstream reads a literal: `rules.py` takes a `StrategyConfig` and so
does `backtest.py`, which is what lets a parameter change be re-validated over
18.6 years before it goes live. See docs/strategy-engine.md.

Adding a rule means adding a field here, defaulted to off, so books already
running keep behaving exactly as they did.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "balanced"

    # ── universe, rebuilt every session from trailing data only ─────────────
    series: Tuple[str, ...] = ("EQ",)
    min_turnover: float = 2e7          # 60-day median, rupees
    turnover_window: int = 60
    top_n_turnover: int = 500
    min_price: float = 20.0            # unadjusted; a split-adjusted floor would
    min_history: int = 250             # wrongly exclude pre-split bars

    # ── regime: one on/off switch for the whole book ────────────────────────
    regime_index_n: int = 200          # names in the equal-weight index
    regime_ma: int = 100
    regime_exit: bool = True           # OFF also closes open positions

    # ── entry ───────────────────────────────────────────────────────────────
    breakout_lookback: int = 250       # 52-week closing high; 0 = all-time high
    trend_template: bool = True        # close > 50 > 150 > 200 DMA, 200 rising
    trend_slope_window: int = 20
    rs_lookback: int = 126
    rs_min_pct: float = 0.80           # top 20% cross-sectional momentum
    sector_top_frac: float = 0.25      # 0 disables the sector overlay
    sector_lookback: int = 63
    max_per_sector: int = 3            # 0 = unlimited
    # measured to reduce excess return; kept as switches, defaulted off
    use_volume_filter: bool = False
    volume_mult: float = 1.5
    use_extension_filter: bool = False
    max_ext_over_50dma: float = 0.30
    require_contraction: bool = False

    # ── exit ────────────────────────────────────────────────────────────────
    atr_len: int = 14
    stop_atr: float = 3.0
    stop_on_close: bool = True         # judged on the close, sold at next open
    # Where the initial stop is measured from. True re-derives it from the fill
    # (stop = fill - stop_atr x ATR), so every trade starts at exactly 1R of
    # risk; the position shrinks or grows with the overnight gap. False keeps
    # the stop the signal advertised the night before, so a gap-up entry simply
    # risks more per share and is sized smaller. True is what was validated.
    stop_from_fill: bool = True
    time_stop: int = 60                # sessions (~3 months)
    trail_atr: Optional[float] = None  # None = no trailing stop (tested, removed)
    trail_after_r: float = 1.5         # only consulted when trail_atr is set
    ema_exit: Optional[int] = None     # None = no moving-average exit
    ema_exit_min_hold: int = 5         # only consulted when ema_exit is set
    stale_exit: int = 5                # sessions with no bar => treat as delisted

    # ── sizing ──────────────────────────────────────────────────────────────
    risk_pct: float = 0.0060           # of current book equity, per trade
    max_positions: int = 12
    max_weight: float = 0.10           # notional cap per position
    adv_cap: float = 0.05              # <= 5% of 20-day median turnover
    adv_window: int = 20

    # ── frictions ───────────────────────────────────────────────────────────
    buy_charges: float = 0.00147       # brokerage + STT + exchange + stamp + GST
    sell_charges: float = 0.00137
    slippage: float = 0.0020           # per side
    cash_yield: float = 0.05           # idle balance, annualised

    # ── bookkeeping ─────────────────────────────────────────────────────────
    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, blob: str) -> "StrategyConfig":
        """
        Rebuild from a stored blob, ignoring fields this version no longer has.

        A book written by a newer build must not crash an older one, and a field
        added since the row was written falls back to its default — which is why
        new fields are always defaulted to off.
        """
        raw: Dict[str, Any] = json.loads(blob)
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in raw.items() if k in known}
        if "series" in kw and isinstance(kw["series"], list):
            kw["series"] = tuple(kw["series"])
        return cls(**kw)

    def variant(self, **kw: Any) -> "StrategyConfig":
        """A copy with some fields changed — the sweep/backtest entry point."""
        return replace(self, **kw)

    @property
    def lookback_needed(self) -> int:
        """
        Sessions of history the rules need before the first valid signal.

        The live job loads exactly this much plus a margin, rather than the whole
        lake; getting it wrong shows up as a silently empty signal list.
        """
        return max(
            self.breakout_lookback or 0,
            self.rs_lookback,
            self.min_history,
            self.regime_ma,
            200 + self.trend_slope_window,   # the slowest DMA plus its slope
            self.turnover_window,
        )


# ── Named presets: measured points on the risk/return frontier ──────────────
# Full-period figures, 2008-01-16 to 2026-08-21, costs and 5% cash yield included.
PRESETS: Dict[str, StrategyConfig] = {
    # 10.6% CAGR, -6.2% max drawdown, 0 losing years
    "conservative": StrategyConfig(name="conservative", max_positions=10,
                                   risk_pct=0.0040, max_weight=0.07),
    # 14.4% CAGR, -12.3% max drawdown, 3 losing years  <- the default
    "balanced": StrategyConfig(name="balanced"),
    # 19.7% CAGR, -19.5% max drawdown; near-full deployment while the regime is ON
    "aggressive": StrategyConfig(name="aggressive", max_positions=12,
                                 risk_pct=0.50, max_weight=1.0 / 12),
}

DEFAULT = PRESETS["balanced"]


def preset(name: str) -> StrategyConfig:
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; have {sorted(PRESETS)}")
    return PRESETS[name]
