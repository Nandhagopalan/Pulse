"""
A manual book's return must be measured against the equity that preceded it.

Positions reach a manual book through the API, between nightly runs, and a
backdated one arrives having already moved. `advance` is then handed a state
that is past the point the day started from — the holding is there, the cash
that bought it is not — so re-deriving the base from that state buries the
entry-to-yesterday gain in the denominator and reports a flat day on a trade
that is up. `prev_equity` is the recorded base that fixes it.

This is the case that cost the live manual book 1.48 points of a 2.01% return.
"""
from __future__ import annotations

import numpy as np
import pytest
from pipeline.compute.strategy import rules
from pipeline.compute.strategy.book import BookState, Position, advance
from pipeline.compute.strategy.config import preset

CAPITAL = 1_000_000.0
QTY = 3_000            # ~30% of the book, so the two bases differ visibly
ENTRY = 100.0          # what the operator paid, a fortnight ago
YESTERDAY = 130.0      # where it had already got to before the book saw it
TODAY = 143.0          # +10% on the session being advanced


def _market(closes: list[float]) -> tuple[rules.MarketData, rules.Features]:
    """Two sessions of one symbol that does nothing but close where told."""
    t, n = len(closes), 1
    px = np.array(closes, dtype=np.float32).reshape(t, n)
    ones = np.ones((t, n), np.float32)
    md = rules.MarketData(
        symbols=np.array(["TEST"]),
        dates=np.datetime64("2026-08-26") + np.arange(t).astype("timedelta64[D]"),
        open=px, high=px, low=px, close=px, volume=ones, turnover=ones * 1e9,
        raw_close=px, is_eq=np.ones((t, n), bool),
    )
    feats = rules.Features(
        universe=np.ones((t, n), bool), regime=np.ones(t, bool),
        ew_index=ones[:, 0], ew_ma=ones[:, 0], prior_hi=px,
        sma50=px, sma150=px, sma200=px, s200_rising=np.ones((t, n), bool),
        atr=ones, rs_pct=ones, tv20=ones * 1e9,
        sector_ok=np.ones((t, n), bool), sector_id=np.full(n, -1, np.int32),
        group_id=np.full(n, -1, np.int32),
    )
    return md, feats


def _book_with_backdated_position() -> BookState:
    """What `load_state` rebuilds after the API has inserted the trade."""
    cost = ENTRY * QTY * (1 + preset("balanced").buy_charges)
    state = BookState(cash=CAPITAL - cost)
    state.positions[0] = Position(
        symbol="TEST", col=0, entry_date=np.datetime64("2026-08-12"), entry=ENTRY,
        qty=QTY, init_stop=90.0, stop=90.0, r_per_share=10.0, origin="manual",
        peak=YESTERDAY, last_px=YESTERDAY,
    )
    return state


def test_gain_before_the_book_saw_it_is_not_lost():
    """The whole trade shows up, not just the part after it was recorded."""
    md, feats = _market([YESTERDAY, TODAY])
    cfg = preset("balanced")

    day = advance(_book_with_backdated_position(), md, feats, cfg, t=1,
                  manual=True, prev_equity=CAPITAL)

    # Equity is cash plus the mark either way; only the base was ever in doubt.
    assert day.equity == pytest.approx(day.cash + QTY * TODAY)
    assert day.twr_factor == pytest.approx(day.equity / CAPITAL, rel=1e-12)
    # +43% on a 30% position: the whole trade, not the 10% leg the book
    # happened to be watching. Deriving the base would have reported ~3.6%.
    assert day.twr_factor - 1 > 0.12


def test_derived_base_is_the_bug_it_replaces():
    """Without the recorded base, the same day reports the last leg only."""
    md, feats = _market([YESTERDAY, TODAY])
    day = advance(_book_with_backdated_position(), md, feats, preset("balanced"),
                  t=1, manual=True)
    assert day.twr_factor - 1 < 0.04


def test_recorded_base_is_a_no_op_when_nothing_moved_the_book():
    """
    The rules book is untouched between runs, so the two bases agree there —
    which is why this change restates no auto-book history.
    """
    md, feats = _market([YESTERDAY, TODAY])
    cfg = preset("balanced")

    derived = advance(_book_with_backdated_position(), md, feats, cfg, t=1, manual=True)
    state = _book_with_backdated_position()
    matching_base = state.cash + state.market_value(md.close[0])
    recorded = advance(_book_with_backdated_position(), md, feats, cfg, t=1,
                       manual=True, prev_equity=matching_base)

    assert recorded.twr_factor == pytest.approx(derived.twr_factor, rel=1e-12)


def test_a_deposit_still_never_reads_as_performance():
    """Flows belong in the base, whichever base is used."""
    md, feats = _market([YESTERDAY, YESTERDAY])
    state = BookState(cash=CAPITAL)
    day = advance(state, md, feats, preset("balanced"), t=1,
                  net_flow=500_000.0, manual=True, prev_equity=CAPITAL)
    assert day.twr_factor == pytest.approx(1.0, abs=2e-4)  # cash yield only
