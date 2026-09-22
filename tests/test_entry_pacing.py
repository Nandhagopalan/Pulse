"""
The per-session fill cap: queue at most N, record all of them.

Runs on a synthetic market rather than the lake, so it holds in CI where there
are no R2 credentials. What it pins is the contract the live path depends on —
`book.advance` queues only the capped slice, while `day.candidates` still
carries every signal, because `store.persist` writes the full list and marks
only the queued ones pending. If the cap were applied to the candidate list
instead, the strategy_signals table would stop recording what the rules saw.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from pipeline.compute.strategy import rules
from pipeline.compute.strategy.book import BookState, advance
from pipeline.compute.strategy.config import preset

T, N = 400, 40


@pytest.fixture(scope="module")
def market():
    """
    Forty names rising together, so every one of them breaks out at once.

    A market where all the signals arrive on the same session is exactly the
    situation the cap exists for, and it is what the first session after a
    regime turn looks like.
    """
    rng = np.random.default_rng(20260922)
    drift = np.linspace(0, 1.2, T)[:, None]
    noise = np.cumsum(rng.normal(0, 0.004, size=(T, N)), axis=0)
    close = (100 * np.exp(drift + noise)).astype(np.float32)
    high = (close * 1.01).astype(np.float32)
    low = (close * 0.99).astype(np.float32)
    turnover = np.full((T, N), 5e8)
    return rules.MarketData(
        symbols=np.array([f"SYM{i:02d}" for i in range(N)], dtype=object),
        dates=(np.datetime64("2020-01-01") + np.arange(T)).astype("datetime64[D]"),
        open=close, high=high, low=low, close=close,
        volume=np.full((T, N), 1e6), turnover=turnover, raw_close=close,
        is_eq=np.ones((T, N), bool),
    )


def _cfg(cap: int):
    return replace(preset("deployed"), max_new_per_session=cap,
                   min_history=200, min_turnover=0.0)


def _run(market, cfg, sessions=6):
    """Advance a fresh book through the last `sessions` rows; count fills."""
    state = BookState(cash=5_000_000.0)
    feats = rules.compute_features(market, cfg)
    opened, seen = [], []
    for t in range(T - sessions, T):
        day = advance(state, market, feats, cfg, t)
        opened.append(len(day.opened))
        seen.append(len(day.candidates))
    return opened, seen, state


def test_uncapped_fills_every_free_slot(market):
    """0 is off: the book takes as many as the slots and the cash allow."""
    opened, _seen, _state = _run(market, _cfg(0))
    assert max(opened) > 2, f"expected a burst of fills, got {opened}"


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_cap_limits_fills_per_session(market, cap):
    opened, _seen, _state = _run(market, _cfg(cap))
    assert max(opened) <= cap, f"cap {cap} exceeded: {opened}"


def test_every_signal_is_still_reported(market):
    """
    The cap must not hide signals from the caller.

    `store.persist` writes `day.candidates` and marks the queued ones pending,
    so a cap applied to the candidate list would silently shrink the record of
    what the rules actually saw.
    """
    _opened, capped_seen, _s = _run(market, _cfg(2))
    _opened, uncapped_seen, _s = _run(market, _cfg(0))
    assert max(capped_seen) > 2
    assert max(capped_seen) == max(uncapped_seen)


def test_the_queue_is_the_strongest_of_the_session(market):
    """What survives the cap is the head of the ranked list, not an arbitrary slice."""
    cfg = _cfg(2)
    feats = rules.compute_features(market, cfg)
    state = BookState(cash=5_000_000.0)
    for t in range(T - 6, T):
        day = advance(state, market, feats, cfg, t)
        if len(day.candidates) > 2:
            queued = [c.symbol for c in state.pending_entries]
            assert queued == [c.symbol for c in day.candidates[:len(queued)]]
            assert [c.rank for c in state.pending_entries] == sorted(
                c.rank for c in state.pending_entries)
            return
    pytest.fail("no session produced more candidates than the cap")
