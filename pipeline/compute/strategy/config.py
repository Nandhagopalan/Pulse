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
    # NSE lists ETF and fund units in the EQ series, so `series` does not
    # separate them from companies. The distinction is in the ISIN: INE is an
    # equity issue, INF a mutual-fund unit. Left off by default because the
    # sector overlay already excludes them (a fund has no industry label), so a
    # book running today behaves identically either way.
    equity_only: bool = False
    # Ceiling on positions sharing a tracked underlying. Silver is listed by
    # seven AMCs; without this the book can hold seven wrappers of one bet, each
    # sized as though it were an independent position, and the risk engine sees
    # seven names rather than one. 0 disables the cap.
    max_per_group: int = 0

    # ── regime: one on/off switch for the whole book ────────────────────────
    regime_index_n: int = 200          # names in the equal-weight index
    # Length of the average the regime index is judged against — this is the
    # rollback delay, and shortening it is the intuitive fix that does not work.
    # A 50-day average trained at -19% drawdown and delivered -34% out of
    # sample, with a negative median held-out return: it sells the dip and
    # rebuys higher. 100 has survived every independent search run against it.
    regime_ma: int = 100
    regime_exit: bool = True           # OFF also closes open positions
    # Sessions the regime must stay OFF before the book is closed. 0 exits on the
    # first OFF session, which is what has always happened and what makes the
    # regime responsible for 63% of exits.
    #
    # MEASURED AND REJECTED. Waiting for confirmation reads as patience and is
    # the opposite: at 2 sessions max drawdown went from -31.2% to -35.8%, at 5
    # to -42.5%, at 8 to -48.1%. The week it waits is the week the market falls
    # fastest. Kept as a switch only so the result stays reproducible — do not
    # raise it without re-running scripts/experiments.py.
    regime_exit_confirm: int = 0
    # Sessions the regime must have been ON before entries resume. Deliberately
    # asymmetric with the exit: leaving late was measured to deepen drawdowns
    # badly, while arriving late only forgoes the first days of a rally.
    #
    # REJECTED for the shipped presets: cheap, but never measured to pay for
    # itself either. It is the one regime knob that did no harm.
    regime_entry_confirm: int = 0
    # Hysteresis on the switch itself: ON needs the index this far *above* its
    # average, OFF still triggers at the average. A single band separating the
    # two thresholds is what stops a market hovering at its moving average from
    # flipping the whole book on and off week after week.
    #
    # MEASURED AND REJECTED, and instructively so: it passed both training
    # criteria and then lost 7-11 points of return out of sample. The whipsaw it
    # removes is real and costs less than the trend it also removes. See
    # scripts/whipsaw.py — this is the clearest overfit in the record.
    regime_band: float = 0.0
    # Sessions over which position size ramps back up after the regime turns ON.
    # 0 deploys at full size immediately; a ramp buys in gradually while a
    # recovery is still unproven.
    #
    # MEASURED AND REJECTED. Ramping over 10 sessions cut CAGR from 14.7% to
    # 10.7% and barely moved drawdown (-31.2% to -26.8%); longer ramps were
    # worse still. The first days of a regime turn carry a large share of the
    # return, so arriving gradually forfeits most of what the switch is for.
    ramp_sessions: int = 0

    # ── entry ───────────────────────────────────────────────────────────────
    breakout_lookback: int = 250       # 52-week closing high; 0 = all-time high
    trend_template: bool = True        # close > 50 > 150 > 200 DMA, 200 rising
    trend_slope_window: int = 20
    rs_lookback: int = 126
    rs_min_pct: float = 0.80           # top 20% cross-sectional momentum
    sector_top_frac: float = 0.25      # 0 disables the sector overlay
    # What to do with a name that has no sector label. True excludes it, which
    # is what the overlay has always done and is why it behaved as a coverage
    # filter rather than a sector filter. Labels now cover 96% of listed names
    # but only 45% of delisted ones, so excluding the unlabeled would screen the
    # backtest toward survivors. False lets them through on momentum alone: they
    # skip the sector test rather than failing it, which biases entries slightly
    # toward the dead and is the conservative direction to err in.
    require_sector_label: bool = True
    # Let a sector or index ETF stand in as its own sector, so it is ranked and
    # gated by the overlay exactly as an equity sector is. This is what makes
    # "buy the sector when the sector is strong" expressible without a stock
    # having to break out. Commodity, cash and debt wrappers are excluded from
    # this: silver is not a sector, and letting it compete for a sector slot
    # would be a different strategy under the same name.
    etf_as_sector: bool = False
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
    # Sessions before a position is closed regardless of how it is doing. None
    # disables it: measured to truncate the winners this edge depends on, since
    # the right tail keeps running well past three months. `balanced` keeps the
    # 60-session stop it was validated with; `deployed` does not have one.
    time_stop: Optional[int] = 60
    trail_atr: Optional[float] = None  # None = no trailing stop (tested, removed)
    trail_after_r: float = 1.5         # only consulted when trail_atr is set
    ema_exit: Optional[int] = None     # None = no moving-average exit
    ema_exit_min_hold: int = 5         # only consulted when ema_exit is set
    stale_exit: int = 5                # sessions with no bar => treat as delisted
    # Trailing exit on a *weekly* close below this weekly EMA. Judged only on
    # week-ending sessions, so an intra-week dip through the line is ignored —
    # which is what separates a weekly stop from a noisy daily one. None = off.
    weekly_ema_exit: Optional[int] = None

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
# The first three assume 5% on idle cash, which at ~18-34% deployment is a large
# part of what they return; `deployed` assumes nothing on cash and earns its
# return from the market instead. That difference matters more than any
# parameter below, so read the two groups as different instruments rather than
# as points on one curve.
PRESETS: Dict[str, StrategyConfig] = {
    # 10.6% CAGR, -6.2% max drawdown, 0 losing years
    "conservative": StrategyConfig(name="conservative", max_positions=10,
                                   risk_pct=0.0040, max_weight=0.07),
    # 14.4% CAGR, -12.3% max drawdown, 3 losing years  <- the default
    "balanced": StrategyConfig(name="balanced"),
    # 19.7% CAGR, -19.5% max drawdown; near-full deployment while the regime is ON
    "aggressive": StrategyConfig(name="aggressive", max_positions=12,
                                 risk_pct=0.50, max_weight=1.0 / 12),
    # 16.98% CAGR, -24.68% max drawdown, 5 losing years, 0% on idle cash.
    # 23.06% CAGR since 2013 against the NIFTY MIDCAP 100's 16.48%, at half its
    # drawdown; held out on 2024-2026 it returned 14.33%, 17.89% and 31.61%.
    #
    # This is rung B of a ladder, not the winner of a search. Across 324
    # aggressive configurations the rank correlation between training CAGR and
    # held-out CAGR was -0.152 — training rank does not predict out-of-sample
    # rank — so the *shape* here is what survived independent validation
    # (100-day regime, weekly EMA20 trail, no time stop) and the size is a
    # declared risk appetite. 0.0055 is the same book inside a 20% drawdown
    # ceiling; nothing else should move without re-running scripts/ladder.py.
    #
    # Deployment is capped by cash, not by the per-name limit: 12 slots at 12.5%
    # is 150% of equity, so the book fills every slot the rules offer and stops
    # when it runs out of money. Average exposure 52%, p95 100%.
    "deployed": StrategyConfig(
        name="deployed",
        risk_pct=0.0080, max_positions=12, max_weight=0.125,
        weekly_ema_exit=20, time_stop=None,
        cash_yield=0.0, max_per_group=1,
        sector_top_frac=0.0, max_per_sector=0, require_sector_label=False,
    ),
}

DEFAULT = PRESETS["balanced"]


def preset(name: str) -> StrategyConfig:
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; have {sorted(PRESETS)}")
    return PRESETS[name]
