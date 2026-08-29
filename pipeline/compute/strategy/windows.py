"""
Trailing-window helpers over [dates x symbols] matrices.

Time runs down the rows, unlike compute/analytics.py, which is symbol-major.
The orientation is deliberate: every rule here is a rolling window along time,
and keeping time contiguous makes those O(n) instead of a strided gather.

Two properties everything in this file holds to:

  * **Trailing only.** Row t is a function of rows <= t. There is no way to
    express a forward-looking window with these primitives, which is the point —
    lookahead in a backtest is silent and fatal.
  * **Bounded memory.** The obvious `sliding_window_view` + `nanmedian` spelling
    materialises a [T x N x w] intermediate; over the full lake that is terabytes
    and the process is simply killed. Everything below is either O(N) working
    set or explicitly chunked.

These reproduce pandas' rolling/ewm semantics (see tests/test_windows.py) so the
validated backtest and the live path compute identical numbers, without making
pandas a runtime dependency of the pipeline.
"""
from __future__ import annotations

import warnings

import numpy as np

# Symbols per chunk in the median path. 256 x 5000 x 60 float64 is ~600 MB of
# peak intermediate, which is the most we want a nightly job to reserve.
_MEDIAN_CHUNK = 256


def _empty_like(a: np.ndarray) -> np.ndarray:
    return np.full(a.shape, np.nan, dtype=np.float32)


def roll_max(a: np.ndarray, w: int) -> np.ndarray:
    """
    Trailing max over w rows, NaN if the window is not fully populated.

    Uses `np.maximum`, not `np.fmax`: NaN must propagate so a symbol that did not
    trade inside the window yields NaN rather than a high taken from a partial
    window. That matches pandas' `min_periods=w`.
    """
    T = a.shape[0]
    out = _empty_like(a)
    if w > T:
        return out
    acc = a[w - 1:].astype(np.float32, copy=True)
    for k in range(1, w):
        acc = np.maximum(acc, a[w - 1 - k: T - k])
    out[w - 1:] = acc
    return out


def roll_min(a: np.ndarray, w: int) -> np.ndarray:
    """Trailing min over w rows; NaN semantics as in roll_max."""
    T = a.shape[0]
    out = _empty_like(a)
    if w > T:
        return out
    acc = a[w - 1:].astype(np.float32, copy=True)
    for k in range(1, w):
        acc = np.minimum(acc, a[w - 1 - k: T - k])
    out[w - 1:] = acc
    return out


def roll_mean(a: np.ndarray, w: int, min_periods: int | None = None) -> np.ndarray:
    """
    Trailing mean ignoring NaN, via cumulative sums.

    Needs `min_periods` valid observations in the window, defaulting to 60% of
    it — a moving average should survive the odd untraded session rather than
    punching a hole in the series.
    """
    mp = min_periods if min_periods is not None else max(2, int(w * 0.6))
    T = a.shape[0]
    out = np.full(a.shape, np.nan, dtype=np.float32)
    if T == 0:
        return out
    valid = np.isfinite(a)
    x = np.where(valid, a, 0.0).astype(np.float64)
    c = valid.astype(np.float64)
    # Prepend a zero row so a window sum is a plain difference of prefixes.
    cx = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(x, axis=0)])
    cc = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(c, axis=0)])
    # The leading window is short, not absent: a value appears as soon as `mp`
    # observations exist, which is before `w` rows have elapsed.
    lo = np.maximum(np.arange(T) - w + 1, 0)
    num = cx[1:] - cx[lo]
    den = cc[1:] - cc[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:] = np.where(den >= mp, num / np.maximum(den, 1.0), np.nan)
    return out


def roll_median(a: np.ndarray, w: int, min_periods: int | None = None) -> np.ndarray:
    """
    Trailing median ignoring NaN, chunked over symbols to bound peak memory.

    Median has no incremental form the way a sum does, so this is the one place
    a windowed view is unavoidable — hence the chunking.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    mp = min_periods if min_periods is not None else max(2, w // 2)
    T, N = a.shape
    out = np.full(a.shape, np.nan, dtype=np.float64)
    if T == 0:
        return out
    # Leading rows have a short window rather than none. There are at most w-1
    # of them, so a plain loop beats special-casing the strided path below.
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN window: masked below
        for t in range(min(w - 1, T)):
            head = a[: t + 1].astype(np.float64)
            cnt = np.count_nonzero(np.isfinite(head), axis=0)
            out[t] = np.where(cnt >= mp, np.nanmedian(head, axis=0), np.nan)
    if w > T:
        return out
    for lo in range(0, N, _MEDIAN_CHUNK):
        hi = min(lo + _MEDIAN_CHUNK, N)
        block = a[:, lo:hi].astype(np.float64)
        sw = sliding_window_view(block, w, axis=0)          # [T-w+1, cols, w]
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN window: masked below
            med = np.nanmedian(sw, axis=2)
            cnt = np.count_nonzero(np.isfinite(sw), axis=2)
        out[w - 1:, lo:hi] = np.where(cnt >= mp, med, np.nan)
    return out


def ema(a: np.ndarray, span: int) -> np.ndarray:
    """
    Exponential moving average down the time axis, NaN-tolerant.

    A missing bar carries the previous value forward instead of breaking the
    chain, and the first observation seeds the average — the same convention as
    `compute/analytics.py::_ema_matrix`, and pandas'
    `ewm(adjust=False, ignore_na=True)`.
    """
    return _ewm(a, 2.0 / (span + 1.0))


def _ewm(a: np.ndarray, alpha: float) -> np.ndarray:
    T, N = a.shape
    out = np.full(a.shape, np.nan, dtype=np.float32)
    prev = np.full(N, np.nan, dtype=np.float64)
    for t in range(T):
        v = a[t].astype(np.float64)
        has = np.isfinite(v)
        seed = has & ~np.isfinite(prev)
        cont = has & np.isfinite(prev)
        prev[seed] = v[seed]
        prev[cont] = v[cont] * alpha + prev[cont] * (1.0 - alpha)
        out[t] = prev
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, w: int = 14) -> np.ndarray:
    """Wilder's ATR: an EMA of true range with alpha = 1/w."""
    prev_close = np.vstack([np.full((1, close.shape[1]), np.nan, np.float32), close[:-1]])
    tr = np.fmax(
        high - low,
        np.fmax(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    return _ewm(tr, 1.0 / w)


def pct_rank(a: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """
    Cross-sectional percentile (0..1] per row, over `valid` entries only.

    Ties take the average rank, matching pandas' default. Entries outside
    `valid`, and NaN, come back as NaN so a filter on the result excludes them
    without a separate mask.
    """
    T, _N = a.shape
    out = np.full(a.shape, np.nan, dtype=np.float32)
    for t in range(T):
        m = valid[t] & np.isfinite(a[t])
        n = int(m.sum())
        if n == 0:
            continue
        v = a[t][m]
        _uniq, inv, counts = np.unique(v, return_inverse=True, return_counts=True)
        # average rank of each distinct value: cumulative count minus half its
        # own run, plus the half-step that makes ranks 1-based
        ends = np.cumsum(counts)
        avg = ends - (counts - 1) / 2.0
        out[t, m] = (avg[inv] / n).astype(np.float32)
    return out


def rank_desc_first(a: np.ndarray, w_valid: np.ndarray | None = None) -> np.ndarray:
    """
    Per-row rank, largest first, ties broken by column order (pandas' "first").

    Used to pick the N most-traded names each session. Ranks rather than a
    threshold because the cut has to be a fixed *count* — an absolute rupee
    floor would admit thousands of names in 2026 and a handful in 2008. NaN
    ranks last and comes back as +inf, so a `<= n` test excludes it.
    """
    T, _N = a.shape
    out = np.full(a.shape, np.inf, dtype=np.float32)
    for t in range(T):
        v = a[t]
        m = np.isfinite(v)
        if w_valid is not None:
            m = m & w_valid[t]
        idx = np.flatnonzero(m)
        if idx.size == 0:
            continue
        order = idx[np.argsort(-v[idx], kind="stable")]
        out[t, order] = np.arange(1, order.size + 1, dtype=np.float32)
    return out


def weekly_ema(close: np.ndarray, dates: np.ndarray, span: int):
    """
    Weekly EMA, forward-filled to daily, plus a mask of week-closing sessions.

    A weekly stop has to be judged on a *weekly* close, not on any day that
    happens to dip below the line — that is the whole reason for using one. So
    this returns two things: the EMA of weekly closes carried forward so it can
    be read on any session, and the mask saying which sessions actually end a
    week and may therefore trigger the exit.

    No lookahead: the value at a week-ending session includes that week's close,
    which is known at the moment the decision is taken, and nothing later.
    """
    weeks = dates.astype("datetime64[W]")
    _uw, widx = np.unique(weeks, return_inverse=True)
    T, N = close.shape
    nw = int(widx.max()) + 1 if T else 0

    # Last session of each week is the week's close.
    last_row = np.full(nw, -1, np.int64)
    for t in range(T):
        last_row[widx[t]] = t
    week_end = np.zeros(T, bool)
    week_end[last_row[last_row >= 0]] = True

    wk_close = close[last_row[last_row >= 0]]          # [nw x N]
    wk = ema(wk_close, span)                            # EMA down the week axis

    # Carry each week's value forward over the days that follow it.
    out = np.full((T, N), np.nan, np.float32)
    out[last_row[last_row >= 0]] = wk
    for t in range(1, T):
        if not week_end[t]:
            out[t] = out[t - 1]
    return out, week_end
