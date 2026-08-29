"""
One session of the paper book: fills, ageing, exits, mark to market.

`advance()` is the only implementation of a trading day. The backtest loops over
it for 18.6 years; the nightly job calls it once, for the session that just
closed. Neither has its own copy of the mechanics — the same reasoning that puts
the rules in one module puts the bookkeeping in one function.

Sequence within a session, and the order matters:

  1. idle cash accrues, then any deposit or withdrawal settles
  2. yesterday's exits fill at today's open
  3. yesterday's entries fill at today's open, subject to slots, sector cap,
     liquidity and cash
  4. surviving positions age; exits are decided on today's close
  5. tomorrow's candidates are generated from today's close

Fills happen before exits are evaluated so a position closed this morning frees
its slot for tomorrow, not today — which is what a real book does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import rules
from .config import StrategyConfig
from .rules import Candidate, Features, MarketData


@dataclass
class Position:
    symbol: str
    col: int
    entry_date: np.datetime64
    entry: float
    qty: int
    init_stop: float
    stop: float
    r_per_share: float
    sector: Optional[str] = None
    config_version: int = 1
    origin: str = "auto"
    bars: int = 0
    stale: int = 0
    peak: float = 0.0
    last_px: float = 0.0
    id: Optional[int] = None          # set once persisted
    exit_date: Optional[np.datetime64] = None
    exit: float = 0.0
    reason: str = ""
    pnl: float = 0.0
    ret: float = 0.0

    @property
    def r_multiple(self) -> float:
        risk = self.r_per_share * self.qty
        return self.pnl / risk if risk > 0 else 0.0


@dataclass
class BookState:
    """Everything that carries from one session to the next."""
    cash: float
    positions: Dict[int, Position] = field(default_factory=dict)
    pending_entries: List[Candidate] = field(default_factory=list)
    pending_exits: List[Tuple[int, str]] = field(default_factory=list)
    config_version: int = 1
    skipped: Dict[str, int] = field(default_factory=lambda: {
        "slots": 0, "sector_cap": 0, "size": 0, "no_bar": 0, "group_cap": 0})

    def market_value(self, close_row: np.ndarray) -> float:
        return sum(
            p.qty * (float(close_row[j]) if np.isfinite(close_row[j]) else p.last_px)
            for j, p in self.positions.items()
        )


@dataclass
class DayResult:
    """What one session produced — the row that goes into strategy_state."""
    date: np.datetime64
    equity: float
    cash: float
    deployed: float
    n_open: int
    regime_on: bool
    net_flow: float
    twr_factor: float                 # daily return with the flow removed
    opened: List[Position] = field(default_factory=list)
    closed: List[Position] = field(default_factory=list)
    candidates: List[Candidate] = field(default_factory=list)


def advance(state: BookState, data: MarketData, feats: Features, cfg: StrategyConfig,
            t: int, net_flow: float = 0.0, manual: bool = False,
            prev_equity: Optional[float] = None) -> DayResult:
    """
    Move the book through session `t`. Mutates `state`; returns the day's row.

    `manual` makes the engine advisory instead of executing. A manual book is
    the operator's own record: positions arrive and leave through the API, and
    all this does is age them, ratchet the stop, mark them to market and record
    what the rules *would* do in `pending_exit`. Nothing opens or closes by
    itself. Candidates are still produced, as suggestions.

    Both modes share one function deliberately. A manual book that aged its
    positions differently from the rules book would make the comparison between
    them meaningless, and comparing them is the whole reason for running two.

    `prev_equity` is the previous session's closing equity, as recorded. Pass it
    whenever the book was reconstructed from storage: between two runs the API
    can have added a position to a manual book, and the reconstruction then
    already contains it. Re-deriving the base from that state values the new
    holding at yesterday's close while the cash that bought it has gone, so the
    whole entry-to-yesterday gain lands in the denominator and is never counted
    as return — a backdated trade enters the book already up and the book never
    says so. The recorded equity is the base that actually preceded the day; a
    position arriving at cost is value-neutral against it, which leaves its
    entire P&L in the day it appears. The backtest carries state in memory with
    nothing able to mutate it, so it passes nothing and the two agree.
    """
    o, h, c = data.open, data.high, data.close
    if prev_equity is not None:
        equity_start = prev_equity
    else:
        equity_start = state.cash + state.market_value(c[t - 1]) if t > 0 else state.cash

    # ── 1. cash accrues, then flows settle ──────────────────────────────────
    state.cash *= 1.0 + (1.0 + cfg.cash_yield) ** (1.0 / 252.0) - 1.0
    state.cash += net_flow

    opened: List[Position] = []
    closed: List[Position] = []

    # ── 2. yesterday's exits, at today's open ───────────────────────────────
    reason: Optional[str]
    for j, reason in ([] if manual else state.pending_exits):
        pos = state.positions.get(j)
        if pos is None:
            continue
        if not np.isfinite(o[t, j]):
            state.skipped["no_bar"] += 1
            continue                                  # retried tomorrow
        px = float(o[t, j]) * (1.0 - cfg.slippage)
        state.cash += px * pos.qty * (1.0 - cfg.sell_charges)
        close_position(pos, data.dates[t], px, reason, cfg)
        closed.append(pos)
        del state.positions[j]
    state.pending_exits = []

    # ── 3. yesterday's entries, at today's open ─────────────────────────────
    equity_now = state.cash + state.market_value(c[t])
    for cand in ([] if manual else state.pending_entries):
        j = cand.col
        if j in state.positions:
            continue
        if len(state.positions) >= cfg.max_positions:
            state.skipped["slots"] += 1
            continue
        if cfg.max_per_sector > 0 and not _sector_room(state, feats, j, cfg):
            state.skipped["sector_cap"] += 1
            continue
        if cfg.max_per_group > 0 and not _group_room(state, feats, j, cfg):
            state.skipped["group_cap"] += 1
            continue
        if not np.isfinite(o[t, j]):
            state.skipped["no_bar"] += 1
            continue
        px = float(o[t, j]) * (1.0 + cfg.slippage)
        stop = px - cfg.stop_atr * cand.atr if cfg.stop_from_fill else cand.stop
        if stop <= 0 or stop >= px:
            continue
        qty = rules.position_size(equity_now, state.cash, px, stop, cand.turnover_20d, cfg)
        if qty <= 0:
            state.skipped["size"] += 1
            continue
        state.cash -= px * qty * (1.0 + cfg.buy_charges)
        pos = Position(
            symbol=cand.symbol, col=j, entry_date=data.dates[t], entry=px, qty=qty,
            init_stop=stop, stop=stop, r_per_share=px - stop, sector=cand.sector,
            config_version=state.config_version, peak=px, last_px=px,
        )
        state.positions[j] = pos
        opened.append(pos)
    state.pending_entries = []

    # ── 4. age survivors, decide exits on today's close ─────────────────────
    regime_on = bool(feats.regime[t])
    for j in list(state.positions):
        pos = state.positions[j]
        close_t = c[t, j]
        if np.isfinite(close_t):
            pos.stale = 0
            pos.last_px = float(close_t)
            pos.bars += 1
            hi = h[t, j]
            pos.peak = max(pos.peak, float(hi) if np.isfinite(hi) else float(close_t))
            pos.stop = rules.trail_stop(pos.stop, pos.peak, float(feats.atr[t, j]),
                                        pos.entry, pos.r_per_share, cfg)
        else:
            pos.stale += 1

        reason = rules.exit_reason(
            close=float(close_t) if np.isfinite(close_t) else pos.last_px,
            stop=pos.stop, bars=pos.bars, regime_on=regime_on, stale=pos.stale,
            cfg=cfg,
            ema_value=float(feats.ema_exit[t, j]) if feats.ema_exit is not None else None,
        )
        if reason == rules.EXIT_STALE and not manual:
            # No bar to sell into. Realise at the last traded price rather than
            # holding a slot for ever — almost always a rename or a merger.
            px = pos.last_px * (1.0 - cfg.slippage)
            state.cash += px * pos.qty * (1.0 - cfg.sell_charges)
            close_position(pos, data.dates[t], px, reason, cfg)
            closed.append(pos)
            del state.positions[j]
        elif reason:
            state.pending_exits.append((j, reason))

    # ── 5. tomorrow's candidates ────────────────────────────────────────────
    free = cfg.max_positions - len(state.positions) + len(state.pending_exits)
    candidates: List[Candidate] = []
    if manual:
        # Suggestions only; a manual book never queues anything for filling.
        candidates = rules.entry_candidates(
            data, feats, cfg, t, exclude=list(state.positions))
        state.pending_entries = []
    elif free > 0:
        candidates = rules.entry_candidates(
            data, feats, cfg, t, exclude=list(state.positions))
        state.pending_entries = candidates[:free]

    equity = state.cash + state.market_value(c[t])
    # Time-weighted: the flow must not read as performance. Settled at the start
    # of the day, so it is part of the base the day's return is measured against.
    base = equity_start + net_flow
    twr = equity / base if base > 0 else 1.0

    return DayResult(
        date=data.dates[t], equity=equity, cash=state.cash,
        deployed=(equity - state.cash) / equity if equity > 0 else 0.0,
        n_open=len(state.positions), regime_on=regime_on,
        net_flow=net_flow, twr_factor=twr,
        opened=opened, closed=closed, candidates=candidates,
    )


def _sector_room(state: BookState, feats: Features, col: int, cfg: StrategyConfig) -> bool:
    sid = feats.sector_id[col]
    if sid < 0:
        return True
    held = sum(1 for k in state.positions if feats.sector_id[k] == sid)
    return held < cfg.max_per_sector


def _group_room(state: BookState, feats: Features, col: int, cfg: StrategyConfig) -> bool:
    """Whether another wrapper on the same underlying may be opened."""
    gid = feats.group_id[col]
    if gid < 0:
        return True
    held = sum(1 for k in state.positions if feats.group_id[k] == gid)
    return held < cfg.max_per_group


def close_position(pos: Position, date, px: float, reason: str, cfg: StrategyConfig) -> None:
    cost = pos.entry * (1.0 + cfg.buy_charges) * pos.qty
    proceeds = px * (1.0 - cfg.sell_charges) * pos.qty
    pos.exit_date, pos.exit, pos.reason = date, px, reason
    pos.pnl = proceeds - cost
    pos.ret = pos.pnl / cost if cost else 0.0


def time_weighted_return(factors: np.ndarray) -> float:
    """Chain-link daily factors into a total return, immune to cash flows."""
    return float(np.prod(factors)) - 1.0


def annualised(factors: np.ndarray, years: float) -> float:
    if years <= 0:
        return 0.0
    return float(np.prod(factors)) ** (1.0 / years) - 1.0
