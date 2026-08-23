"""
Pin the rolling helpers to pandas' semantics.

The strategy was validated with a pandas-backed implementation; the pipeline
runs a numpy one, because pandas is not a runtime dependency (see
pyproject.toml — it is in the dev group for exactly this file). If the two ever
diverge, the strategy being traded stops being the strategy that was tested, and
nothing else in the system would notice.

pandas is therefore the oracle here, not the implementation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.compute.strategy import windows as W

T, N = 400, 60


@pytest.fixture(scope="module")
def data():
    """Random walks with NaN gaps, so untraded sessions are covered too."""
    rng = np.random.default_rng(20260823)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(T, N)), axis=0))
    high = close * (1 + np.abs(rng.normal(0, 0.01, size=(T, N))))
    low = close * (1 - np.abs(rng.normal(0, 0.01, size=(T, N))))
    vol = rng.lognormal(12, 1.5, size=(T, N))
    # ~4% of bars missing, plus one symbol that stops trading entirely
    gaps = rng.random((T, N)) < 0.04
    for m in (close, high, low, vol):
        m[gaps] = np.nan
    close[200:, 7] = high[200:, 7] = low[200:, 7] = vol[200:, 7] = np.nan
    return (close.astype(np.float32), high.astype(np.float32),
            low.astype(np.float32), vol.astype(np.float32))


def _close(got, want, tol=1e-4):
    """NaN positions must agree exactly; finite values to float32 tolerance."""
    g, w = np.asarray(got, float), np.asarray(want, float)
    assert g.shape == w.shape
    np.testing.assert_array_equal(np.isnan(g), np.isnan(w))
    both = ~np.isnan(g)
    if both.any():
        np.testing.assert_allclose(g[both], w[both], rtol=tol, atol=tol)


@pytest.mark.parametrize("w", [5, 20, 250])
def test_roll_max(data, w):
    close = data[0]
    _close(W.roll_max(close, w),
           pd.DataFrame(close).rolling(w, min_periods=w).max().to_numpy())


@pytest.mark.parametrize("w", [5, 20, 250])
def test_roll_min(data, w):
    close = data[0]
    _close(W.roll_min(close, w),
           pd.DataFrame(close).rolling(w, min_periods=w).min().to_numpy())


@pytest.mark.parametrize("w", [50, 150, 200])
def test_roll_mean(data, w):
    close = data[0]
    mp = max(2, int(w * 0.6))
    _close(W.roll_mean(close, w),
           pd.DataFrame(close).rolling(w, min_periods=mp).mean().to_numpy())


@pytest.mark.parametrize("w", [20, 60])
def test_roll_median(data, w):
    vol = data[3]
    mp = max(2, w // 2)
    _close(W.roll_median(vol, w),
           pd.DataFrame(vol).rolling(w, min_periods=mp).median().to_numpy())


def test_roll_median_chunk_boundary(data):
    """The chunked path must give the same answer as a single chunk."""
    vol = data[3]
    full = W.roll_median(vol, 60)
    original, W._MEDIAN_CHUNK = W._MEDIAN_CHUNK, 7
    try:
        chunked = W.roll_median(vol, 60)
    finally:
        W._MEDIAN_CHUNK = original
    _close(chunked, full)


@pytest.mark.parametrize("span", [20, 50])
def test_ema(data, span):
    close = data[0]
    _close(W.ema(close, span),
           pd.DataFrame(close).ewm(span=span, adjust=False, ignore_na=True).mean().to_numpy())


def test_atr(data):
    close, high, low, _ = data
    prev = np.vstack([np.full((1, N), np.nan, np.float32), close[:-1]])
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev), np.abs(low - prev)))
    want = pd.DataFrame(tr).ewm(alpha=1 / 14, adjust=False, ignore_na=True).mean().to_numpy()
    _close(W.atr(high, low, close, 14), want)


def test_pct_rank(data):
    close = data[0]
    ret = np.full_like(close, np.nan)
    ret[126:] = close[126:] / close[:-126] - 1.0
    valid = np.isfinite(close)
    want = pd.DataFrame(np.where(valid, ret, np.nan)).rank(axis=1, pct=True).to_numpy()
    _close(W.pct_rank(ret, valid), want, tol=1e-6)


def test_pct_rank_handles_ties():
    """Ties take the average rank, as pandas does by default."""
    a = np.array([[1.0, 1.0, 2.0, 3.0]], dtype=np.float32)
    valid = np.ones_like(a, dtype=bool)
    _close(W.pct_rank(a, valid),
           pd.DataFrame(a).rank(axis=1, pct=True).to_numpy(), tol=1e-6)


def test_no_lookahead():
    """
    Truncating the input must not change any earlier row.

    This is the property that makes a backtest trustworthy: if row t depended on
    anything after t, cutting the series short would move it.
    """
    rng = np.random.default_rng(7)
    a = rng.normal(size=(300, 5)).astype(np.float32).cumsum(axis=0) + 100
    cut = 220
    for fn in (lambda x: W.roll_max(x, 20),
               lambda x: W.roll_min(x, 20),
               lambda x: W.roll_mean(x, 50),
               lambda x: W.roll_median(x, 20),
               lambda x: W.ema(x, 20)):
        _close(fn(a)[:cut], fn(a[:cut]))


def test_rank_desc_first(data):
    """Largest-first ranking with ties by column order, as pandas 'first' does."""
    vol = data[3]
    got = W.rank_desc_first(vol)
    want = pd.DataFrame(-vol.astype(np.float64)).rank(axis=1, method="first").to_numpy()
    finite = np.isfinite(want)
    np.testing.assert_allclose(got[finite], want[finite])
    assert np.isinf(got[~finite]).all(), "NaN inputs must rank as +inf, never inside a top-N cut"


def test_rank_desc_first_ties():
    a = np.array([[5.0, 5.0, 1.0, np.nan]], dtype=np.float32)
    got = W.rank_desc_first(a)
    np.testing.assert_allclose(got[0, :3], [1.0, 2.0, 3.0])
    assert np.isinf(got[0, 3])
