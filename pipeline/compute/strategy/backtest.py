"""
Historical harness: `book.advance()` in a loop.

This exists so a parameter change can be re-validated over 18.6 years before it
is traded. It owns no trading logic of its own — the rules live in `rules.py`
and a session's mechanics in `book.py`, and this module only drives the clock
and totals the result. That is what keeps the tested strategy and the traded
strategy the same thing.

Verified against the reference run: tests/test_backtest_fidelity.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .book import BookState, Position, advance, annualised
from .config import StrategyConfig
from .rules import Features, MarketData


@dataclass
class Result:
    dates: np.ndarray
    equity: np.ndarray
    invested: np.ndarray
    n_open: np.ndarray
    twr: np.ndarray                     # daily factors, for flow-safe returns
    trades: List[Position]
    open_positions: Dict[int, Position] = field(default_factory=dict)
    skipped: Dict[str, int] = field(default_factory=dict)
    cfg: Optional[StrategyConfig] = None
    capital: float = 0.0


def run(data: MarketData, feats: Features, cfg: StrategyConfig,
        capital: float = 5_000_000.0,
        start: Optional[str] = None, end: Optional[str] = None) -> Result:
    dates = data.dates
    t0 = int(np.searchsorted(dates, np.datetime64(start))) if start else 0
    t1 = int(np.searchsorted(dates, np.datetime64(end))) if end else len(dates)
    # Nothing can trade before the slowest indicator has warmed up.
    t0 = max(t0, cfg.lookback_needed + 10)
    if t1 <= t0:
        raise ValueError("empty backtest window")

    state = BookState(cash=capital)
    closed: List[Position] = []
    n = t1 - t0
    equity = np.zeros(n)
    invested = np.zeros(n)
    n_open = np.zeros(n)
    twr = np.ones(n)

    for i, t in enumerate(range(t0, t1)):
        day = advance(state, data, feats, cfg, t)
        closed.extend(day.closed)
        equity[i] = day.equity
        invested[i] = day.deployed
        n_open[i] = day.n_open
        twr[i] = day.twr_factor

    return Result(dates=dates[t0:t1], equity=equity, invested=invested,
                  n_open=n_open, twr=twr, trades=closed,
                  open_positions=state.positions, skipped=state.skipped,
                  cfg=cfg, capital=capital)


def summarise(res: Result) -> dict:
    eq, dts, trs = res.equity, res.dates, res.trades
    years = (dts[-1] - dts[0]).astype("timedelta64[D]").astype(float) / 365.25
    dd = eq / np.maximum.accumulate(eq) - 1.0
    ret = np.diff(eq) / eq[:-1]
    wins = [t for t in trs if t.pnl > 0]
    losses = [t for t in trs if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    rmults = [t.r_multiple for t in trs if t.r_per_share > 0 and t.qty > 0]
    holds = [t.bars for t in trs]
    return {
        "years": years,
        "end": float(eq[-1]),
        # Chain-linked, so this stays correct if the book ever sees a cash flow.
        "cagr": annualised(res.twr, years),
        "max_dd": float(dd.min()),
        "sharpe": float(ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0.0,
        "n_trades": len(trs),
        "win_rate": len(wins) / len(trs) if trs else 0.0,
        "payoff": (np.mean([t.ret for t in wins]) / abs(np.mean([t.ret for t in losses])))
                  if wins and losses else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "expectancy_r": float(np.mean(rmults)) if rmults else 0.0,
        "median_hold": float(np.median(holds)) if holds else 0.0,
        "exposure": float(res.invested.mean()),
        "skipped": dict(res.skipped),
    }


def by_year(res: Result) -> List[tuple]:
    """(year, return, ending equity, worst drawdown that year)."""
    years = res.dates.astype("datetime64[Y]").astype(int) + 1970
    out = []
    for y in np.unique(years):
        m = years == y
        e = res.equity[m]
        prev = res.equity[np.flatnonzero(m)[0] - 1] if np.flatnonzero(m)[0] > 0 else res.capital
        dd = e / np.maximum.accumulate(e) - 1.0
        out.append((int(y), float(e[-1] / prev - 1.0), float(e[-1]), float(dd.min())))
    return out
