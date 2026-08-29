"""
The strategy itself. This module is the single definition of what the strategy
*is* — every rule, and nothing else.

Two callers import it, and that is the whole point of the layout:

    strategy/__init__.py   tonight's signals and exits, for the live paper book
    strategy/backtest.py   the same functions replayed over 18.6 years

If the live path and the validation path had separate copies of these rules they
would drift, and the strategy being traded would quietly stop being the strategy
that was tested. Re-running the historical validation must exercise exactly the
code that produces tomorrow morning's orders.

Nothing here touches the database, the network, or the clock. It takes matrices
and a config, and returns decisions — which is what makes it testable.

No-lookahead contract: every value at row t derives from rows <= t. Signals are
computed on a session's close and are always acted on at the *next* open, which
the callers are responsible for honouring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import windows as W
from .config import StrategyConfig

# Exit reasons, in the order they are tested. Stored on the position, so they
# are part of the data contract, not just diagnostics.
EXIT_STOP = "stop"
EXIT_TIME = "time"
EXIT_REGIME = "regime"
EXIT_STALE = "stale"
EXIT_EMA = "ema"
EXIT_WEEKLY = "weekly"


@dataclass
class MarketData:
    """Adjusted OHLCV as [dates x symbols], plus what the universe filter needs."""
    symbols: np.ndarray                  # [N] str
    dates: np.ndarray                    # [T] datetime64[D]
    open: np.ndarray                     # [T x N] float32, split-adjusted
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray                 # rupees, unadjusted
    raw_close: np.ndarray                # unadjusted, for the penny-stock floor
    is_eq: np.ndarray                    # [T x N] bool
    sector: Optional[np.ndarray] = None  # [N] str, may be None
    is_equity: Optional[np.ndarray] = None  # [N] bool, False for fund units
    group: Optional[np.ndarray] = None      # [N] str, the tracked underlying

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape


@dataclass
class Features:
    """Everything the rules need, precomputed once over the whole window."""
    universe: np.ndarray      # [T x N] bool
    regime: np.ndarray        # [T] bool
    ew_index: np.ndarray      # [T] the equal-weight regime index
    ew_ma: np.ndarray         # [T] its moving average
    prior_hi: np.ndarray      # [T x N] breakout reference, today excluded
    sma50: np.ndarray
    sma150: np.ndarray
    sma200: np.ndarray
    s200_rising: np.ndarray
    atr: np.ndarray
    rs_pct: np.ndarray        # cross-sectional momentum rank, 0..1
    tv20: np.ndarray          # 20-day median turnover, for the ADV cap
    sector_ok: np.ndarray     # [T x N] bool
    sector_id: np.ndarray     # [N] int32, -1 where unknown
    group_id: np.ndarray      # [N] int32, -1 where the name stands alone
    ema_exit: Optional[np.ndarray] = None
    volmed: Optional[np.ndarray] = None
    contracted: Optional[np.ndarray] = None
    off_run: Optional[np.ndarray] = None    # [T] consecutive sessions regime OFF
    on_run: Optional[np.ndarray] = None     # [T] consecutive sessions regime ON
    wk_ema: Optional[np.ndarray] = None     # [T x N] weekly EMA, held forward
    week_end: Optional[np.ndarray] = None   # [T] bool, session closes a week


@dataclass(frozen=True)
class Candidate:
    """One entry signal, sized. Mirrors a row of strategy_signals."""
    col: int                  # column index into MarketData
    symbol: str
    rank: int                 # 1 = strongest; fills happen in this order
    ref_close: float          # the close that triggered it
    stop: float
    stop_pct: float
    atr: float
    rs_pct: float
    sector: Optional[str]
    turnover_20d: float


def compute_features(data: MarketData, cfg: StrategyConfig) -> Features:
    """
    All trailing indicators, the point-in-time universe, and the regime.

    Cost is dominated by the two rolling medians over turnover; everything else
    is a cumulative sum or a single pass down the time axis.
    """
    close, high, low = data.close, data.high, data.low
    T, N = data.shape

    tv_slow = W.roll_median(data.turnover, cfg.turnover_window)
    tv20 = W.roll_median(data.turnover, cfg.adv_window)
    sma50 = W.roll_mean(close, 50)
    sma150 = W.roll_mean(close, 150)
    sma200 = W.roll_mean(close, 200)
    atr = W.atr(high, low, close, cfg.atr_len)

    # Breakout reference: the highest close *before* today, so the comparison is
    # strictly "higher than anything previously seen".
    if cfg.breakout_lookback == 0:
        running = np.maximum.accumulate(np.nan_to_num(close, nan=-np.inf), axis=0)
        hi = np.where(np.isfinite(running), running, np.nan).astype(np.float32)
    else:
        hi = W.roll_max(close, cfg.breakout_lookback)
    prior_hi = np.vstack([np.full((1, N), np.nan, np.float32), hi[:-1]])

    k = cfg.trend_slope_window
    s200_rising = np.vstack([np.zeros((k, N), bool), sma200[k:] > sma200[:-k]])

    # ── point-in-time universe ──────────────────────────────────────────────
    nbars = np.cumsum(np.isfinite(close), axis=0)
    rank_tv = W.rank_desc_first(tv_slow)
    universe = (
        data.is_eq
        & np.isfinite(close)
        & (tv_slow >= cfg.min_turnover)
        & (rank_tv <= cfg.top_n_turnover)
        & (data.raw_close >= cfg.min_price)
        & (nbars >= cfg.min_history)
    )
    if cfg.equity_only and data.is_equity is not None:
        universe &= data.is_equity[None, :]

    # ── cross-sectional momentum, ranked within the universe only ───────────
    ret = np.full((T, N), np.nan, np.float32)
    lb = cfg.rs_lookback
    if lb < T:
        with np.errstate(all="ignore"):
            ret[lb:] = close[lb:] / close[:-lb] - 1.0
    rs_pct = W.pct_rank(ret, universe)

    ew_index, ew_ma, regime = _regime(data, rank_tv, cfg)
    off_run, on_run = _runs(regime)
    sector_ok, sector_id = _sector(data, cfg)
    group_id = _groups(data)

    feats = Features(
        universe=universe, regime=regime, ew_index=ew_index, ew_ma=ew_ma,
        prior_hi=prior_hi, sma50=sma50, sma150=sma150, sma200=sma200,
        s200_rising=s200_rising, atr=atr, rs_pct=rs_pct, tv20=tv20,
        sector_ok=sector_ok, sector_id=sector_id, group_id=group_id,
        off_run=off_run, on_run=on_run,
    )

    # Optional filters. Each was measured to *reduce* excess return, so they are
    # off by default and only computed when switched on.
    if cfg.weekly_ema_exit:
        feats.wk_ema, feats.week_end = W.weekly_ema(close, data.dates,
                                                    cfg.weekly_ema_exit)
    if cfg.ema_exit:
        feats.ema_exit = W.ema(close, cfg.ema_exit)
    if cfg.use_volume_filter:
        feats.volmed = W.roll_median(data.volume, 50)
    if cfg.require_contraction:
        with np.errstate(all="ignore"):
            atrp = atr / close
        feats.contracted = atrp <= W.roll_median(atrp, 100)
    return feats


def _runs(regime: np.ndarray):
    """How many sessions the regime has been OFF, and ON, as of each row."""
    T = len(regime)
    off = np.zeros(T, np.int32)
    on = np.zeros(T, np.int32)
    for t in range(T):
        if regime[t]:
            on[t] = (on[t - 1] + 1) if t else 1
        else:
            off[t] = (off[t - 1] + 1) if t else 1
    return off, on


def _regime(data: MarketData, rank_tv: np.ndarray, cfg: StrategyConfig):
    """
    Equal-weight index of the most-traded names, against its own moving average.

    Built from the lake rather than from NIFTY because `index_daily` only starts
    in 2013 (and NIFTY 50 daily in late 2015), while the bars go back to 2007 —
    a regime filter that cannot be evaluated over the crisis it is meant to
    handle is not worth much.
    """
    close = data.close
    T, N = data.shape
    ret1 = np.full((T, N), np.nan, np.float32)
    with np.errstate(all="ignore"):
        ret1[1:] = close[1:] / close[:-1] - 1.0
    # A single mis-adjusted bar must not swamp the average.
    ret1 = np.clip(ret1, -0.5, 0.5)

    members = (rank_tv <= cfg.regime_index_n) & np.isfinite(ret1) & data.is_eq
    num = np.where(members, np.nan_to_num(ret1), 0.0).sum(axis=1)
    den = np.maximum(members.sum(axis=1), 1)
    ew_index = np.cumprod(1.0 + num / den)

    ew_ma = W.roll_mean(ew_index.reshape(-1, 1).astype(np.float32), cfg.regime_ma).ravel()
    if cfg.regime_band > 0:
        # Two thresholds, walked forward: switch ON only once the index clears
        # the average by the band, switch OFF as soon as it loses the average.
        # The state carries, so a market sitting on its average stays where it
        # was rather than flipping with every crossing.
        regime = np.zeros(T, bool)
        on = False
        hi = ew_ma * (1.0 + cfg.regime_band)
        for t in range(T):
            if not np.isfinite(ew_ma[t]):
                regime[t] = False
                continue
            on = ew_index[t] > hi[t] if not on else ew_index[t] >= ew_ma[t]
            regime[t] = on
        return ew_index, ew_ma, regime
    with np.errstate(invalid="ignore"):
        regime = np.nan_to_num(ew_index > ew_ma, nan=False)
    return ew_index, ew_ma, regime.astype(bool)


def _sector(data: MarketData, cfg: StrategyConfig):
    """
    Sector strength, and the id used for the per-sector position cap.

    Caveat worth keeping in view: the labels come from today's NIFTY 500
    constituent file, the only sector map in the lake. The control run (same
    universe, no sector selection) shows the selection effect is real rather
    than survivorship, but the labels still describe today's index.
    """
    T, N = data.shape
    sector_id = np.full(N, -1, np.int32)

    # Effective sector label per column. Equities carry an industry; a fund unit
    # carries the theme it tracks, and when `etf_as_sector` is set the
    # sector-like themes become sectors in their own right. They are kept
    # separate from the equity sectors rather than merged into them: a bank ETF
    # and a basket of bank shares move together but are not the same instrument,
    # and the overlay should be able to rank one above the other.
    labels: List[Optional[str]] = (
        [str(x) if x else None for x in data.sector] if data.sector is not None
        else [None] * N
    )
    if cfg.etf_as_sector and data.group is not None:
        from .data import SECTOR_LIKE
        for i, g in enumerate(data.group):
            if g and str(g) in SECTOR_LIKE:
                labels[i] = f"ETF {g}"
    if all(x is None for x in labels):
        return np.ones((T, N), bool), sector_id

    groups: Dict[str, List[int]] = {}
    for i, name in enumerate(labels):
        if name:
            groups.setdefault(str(name), []).append(i)
    names = sorted(groups)
    for gi, name in enumerate(names):
        for col in groups[name]:
            sector_id[col] = gi

    if cfg.sector_top_frac <= 0 or not names:
        return np.ones((T, N), bool), sector_id

    lb = cfg.sector_lookback
    mom = np.full((T, N), np.nan, np.float32)
    if lb < T:
        with np.errstate(all="ignore"):
            mom[lb:] = data.close[lb:] / data.close[:-lb] - 1.0

    comp = np.full((T, len(names)), np.nan, np.float64)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a sector can be all-NaN early
        for gi, name in enumerate(names):
            comp[:, gi] = np.nanmean(mom[:, groups[name]], axis=1)

    srank = W.pct_rank(comp, np.isfinite(comp))
    strong = srank >= (1.0 - cfg.sector_top_frac)

    # An unlabeled symbol starts out passing or failing according to the config:
    # it cannot be *shown* to sit in a leading sector, but excluding it makes the
    # rule depend on label coverage, and coverage is far better for companies
    # that survived than for those that did not.
    ok = np.zeros((T, N), bool) if cfg.require_sector_label else np.ones((T, N), bool)
    for gi, name in enumerate(names):
        cols = groups[name]
        ok[:, cols] = strong[:, gi][:, None]
    return ok, sector_id


def _groups(data: MarketData) -> np.ndarray:
    """
    Column -> tracked-underlying id, for the duplicate-exposure cap.

    Only fund units are grouped. Two companies in the same industry are two
    businesses and the sector cap already governs how many of them the book
    holds; two silver ETFs from different AMCs are one position wearing two
    tickers, and nothing else in the engine can tell them apart.

    -1 means the name stands alone and the cap never applies to it.
    """
    N = len(data.symbols)
    ids = np.full(N, -1, np.int32)
    if data.group is None:
        return ids
    names = sorted({str(g) for g in data.group if g})
    index = {g: i for i, g in enumerate(names)}
    for i, g in enumerate(data.group):
        if g:
            ids[i] = index[str(g)]
    return ids


def entry_candidates(
    data: MarketData,
    feats: Features,
    cfg: StrategyConfig,
    t: int,
    exclude: Sequence[int] = (),
) -> List[Candidate]:
    """
    Ranked entry signals from session `t`'s close, to be filled at t+1's open.

    Returns every qualifying name, strongest first. Slot, sector-cap, liquidity
    and cash limits are applied by the caller when filling — a signal that could
    not be taken is still worth recording.
    """
    # Entries always require the regime to be on. `regime_exit` is a separate
    # question — whether it also closes positions already held.
    if not feats.regime[t]:
        return []
    # Wait for the recovery to hold before buying into it.
    if (cfg.regime_entry_confirm > 0 and feats.on_run is not None
            and feats.on_run[t] < cfg.regime_entry_confirm):
        return []

    close = data.close[t]
    ok = feats.universe[t].copy()
    ok &= close > feats.prior_hi[t]

    if cfg.trend_template:
        ok &= (close > feats.sma50[t]) & (feats.sma50[t] > feats.sma150[t])
        ok &= (feats.sma150[t] > feats.sma200[t]) & feats.s200_rising[t]

    ok &= feats.rs_pct[t] >= cfg.rs_min_pct

    if cfg.sector_top_frac > 0:
        ok &= feats.sector_ok[t]
    if cfg.use_volume_filter and feats.volmed is not None:
        ok &= data.volume[t] >= cfg.volume_mult * feats.volmed[t]
    if cfg.use_extension_filter:
        with np.errstate(all="ignore"):
            ok &= (close / feats.sma50[t] - 1.0) <= cfg.max_ext_over_50dma
    if cfg.require_contraction and feats.contracted is not None:
        ok &= feats.contracted[t]

    # A stop needs a positive ATR; without one the position cannot be sized.
    ok &= np.isfinite(feats.atr[t]) & (feats.atr[t] > 0)
    if len(exclude):
        ok[np.asarray(exclude, dtype=int)] = False

    cols = np.flatnonzero(ok)
    if cols.size == 0:
        return []
    cols = cols[np.argsort(-feats.rs_pct[t][cols], kind="stable")]

    out: List[Candidate] = []
    for rank, col in enumerate(cols, start=1):
        ref = float(close[col])
        stop = ref - cfg.stop_atr * float(feats.atr[t, col])
        if stop <= 0 or stop >= ref:
            continue
        tv = feats.tv20[t, col]
        out.append(Candidate(
            col=int(col),
            symbol=str(data.symbols[col]),
            rank=rank,
            ref_close=ref,
            stop=stop,
            stop_pct=(ref - stop) / ref,
            atr=float(feats.atr[t, col]),
            rs_pct=float(feats.rs_pct[t, col]),
            sector=str(data.sector[col]) if data.sector is not None and data.sector[col] else None,
            turnover_20d=float(tv) if np.isfinite(tv) else 0.0,
        ))
    return out


def size_limits(equity: float, cash: float, entry_px: float, stop: float,
                turnover_20d: float, cfg: StrategyConfig) -> Dict[str, int]:
    """
    Every cap bearing on one position, as a share count. The smallest binds.

    Returned as a mapping rather than folded straight into a minimum so the
    caller can say *which* limit stopped a fill. "Could not size this position"
    covers two situations that have nothing to do with each other — the book is
    already fully invested, or the market is too thin to take the order — and
    only the second is a problem. A book deploying to 100% by design hits the
    first constantly.

    Empty when the trade is unsizeable at all, which is a degenerate stop rather
    than a cap.
    """
    risk_per_share = entry_px - stop
    if risk_per_share <= 0 or entry_px <= 0:
        return {}
    lim = {
        "risk": int(cfg.risk_pct * equity / risk_per_share),
        "weight": int(cfg.max_weight * equity / entry_px),
        "cash": int(cash / (entry_px * (1.0 + cfg.buy_charges))),
    }
    if turnover_20d > 0:
        lim["liquidity"] = int(cfg.adv_cap * turnover_20d / entry_px)
    return lim


def position_size(equity: float, cash: float, entry_px: float, stop: float,
                  turnover_20d: float, cfg: StrategyConfig) -> int:
    """
    Quantity for one position: risk first, then the caps.

    Size follows from the stop distance, so every position risks the same rupees
    regardless of how volatile the stock is — a calm name gets a large position
    and a wild one a small position. The caps that follow are protective, not
    part of the edge.
    """
    lim = size_limits(equity, cash, entry_px, stop, turnover_20d, cfg)
    return max(min(lim.values()), 0) if lim else 0


def exit_reason(*, close: float, stop: float, bars: int, regime_on: bool,
                stale: int, cfg: StrategyConfig,
                ema_value: Optional[float] = None,
                off_run: int = 0, wk_value: Optional[float] = None,
                is_week_end: bool = False) -> Optional[str]:
    """
    Whether an open position should be closed at the next open, and why.

    Order matters: a position that breaches its stop on the same session the
    regime flips is recorded as a stop, because that is the binding reason and
    it is what the R multiple should be attributed to.
    """
    if stale >= cfg.stale_exit:
        return EXIT_STALE
    if cfg.stop_on_close and np.isfinite(close) and close <= stop:
        return EXIT_STOP
    # A confirmation window means the book rides out a single weak week rather
    # than liquidating into it; the cost is entering a real decline later.
    if cfg.regime_exit and not regime_on and off_run >= max(cfg.regime_exit_confirm, 1):
        return EXIT_REGIME
    if (cfg.weekly_ema_exit and is_week_end and wk_value is not None
            and np.isfinite(wk_value) and np.isfinite(close) and close < wk_value):
        return EXIT_WEEKLY
    if cfg.time_stop is not None and bars >= cfg.time_stop:
        return EXIT_TIME
    if (cfg.ema_exit and bars >= cfg.ema_exit_min_hold and ema_value is not None
            and np.isfinite(ema_value) and np.isfinite(close) and close < ema_value):
        return EXIT_EMA
    return None


def trail_stop(current_stop: float, peak: float, atr_value: float,
               entry: float, r_per_share: float, cfg: StrategyConfig) -> float:
    """
    Ratchet the stop upward, when a trailing stop is configured at all.

    Off by default: trailing was measured to truncate winners and cost return,
    because this edge grows with holding period rather than decaying.
    """
    if cfg.trail_atr is None or not np.isfinite(atr_value):
        return current_stop
    if (peak - entry) < cfg.trail_after_r * r_per_share:
        return current_stop
    return max(current_stop, peak - cfg.trail_atr * atr_value)
