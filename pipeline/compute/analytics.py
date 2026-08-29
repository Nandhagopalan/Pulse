"""
Analytics engine: R2 Parquet → the daily "Pulse" snapshot.

Split of labour is deliberate:

  DuckDB (over the whole 8M-bar lake)   all-time highs, first-listed date, the
                                        corporate-action adjustment join
  numpy  (over a 520-session window)    EMAs, 52-week extremes, breadth counters,
                                        relative strength, sector composites

Anything "all-time" has to see every bar, and that is an aggregate DuckDB does in
one streaming pass. Everything else needs sequential state per symbol (EMAs are
recursive, breadth is a rolling count), which is painful in SQL and trivial as a
vectorised loop over a 520 x ~3000 matrix — about 50 MB of RAM.

The metric definitions mirror server/src/analytics/engine.ts so both stacks
produce comparable numbers while the migration is in flight.
"""
from __future__ import annotations

import warnings
from datetime import date as Date
from typing import Dict, List, Optional

import numpy as np

from ..config import CURATED_DAILY, CURATED_INDEX, s3_uri
from ..ingest import corporate_actions as ca
from ..ingest.industry import INDUSTRY_KEY
from ..ingest.reference import CONSTITUENTS_KEY
from ..sources import nse, r2

# Sessions pulled into memory. Needs HIST + 252 so every point in the breadth
# history has a full 52-week lookback, and >= CANDLES for the chart payload.
WINDOW = 520
HIST = 120      # sessions of breadth history published
CANDLES = 500   # adjusted candles cached per symbol for the chart endpoint

# ── Weekly structure ─────────────────────────────────────────────────────────
# A trendline break is a weekly-timeframe event. The lines that matter are
# anchored years back — well outside the 520-session daily window — and a daily
# chart is too noisy to place them on anyway. Rather than widen WINDOW, which
# would double the adjustment join and every matrix held in memory, the
# structure layer gets its own small load: ~4 years of weekly bars, aggregated
# down in DuckDB so what lands in Python is a fifth of the rows.
WEEKS = 210
K_SWING = 3          # weeks either side that make a weekly high a swing pivot
MIN_SPAN = 12        # weeks a line must cap price before breaking it means anything
BREAK_MARGIN = 0.01  # the weekly close has to clear the line by this much
TOUCH_TOL = 0.03     # a swing high this near the line counts as touching it
MIN_TOUCHES = 3      # anchor, the pivot that sets the slope, and one more
HOLD_WEEKS = 8       # a break stays flagged this long, while price holds above
MIN_TURNOVER = 2e7   # ₹2 cr/week — below this the break is not tradeable

def _uris():
    """
    Dataset locations, honouring `--local` mode.

    Resolved per call rather than at import: the CLI sets the store mode after
    modules are loaded, so constants captured at import time would always point
    at R2 even when the caller asked for the local mirror.
    """
    from ..ingest import backfill
    # Bound directly rather than via an `local = ... is not None` flag, so the
    # None-check narrows the type for the reader and the checker alike.
    root = backfill.LOCAL_ROOT
    if root is not None and not backfill.USE_R2:
        return (str(root / CURATED_DAILY / "*" / "data.parquet"),
                str(root / CURATED_INDEX / "*" / "data.parquet"),
                str(root / ca.ACTIONS_KEY),
                str(root / CONSTITUENTS_KEY),
                str(root / INDUSTRY_KEY))
    return (s3_uri(f"{CURATED_DAILY}/*/data.parquet"),
            s3_uri(f"{CURATED_INDEX}/*/data.parquet"),
            s3_uri(ca.ACTIONS_KEY),
            s3_uri(CONSTITUENTS_KEY),
            s3_uri(INDUSTRY_KEY))


def _ema_matrix(values: np.ndarray, period: int) -> np.ndarray:
    """
    EMA per row (symbol) across columns (sessions).

    Missing bars carry the previous EMA forward rather than breaking the chain,
    and the first observation seeds the average — matching engine.ts, where a
    NaN input yields the previous output instead of a gap.
    """
    k = 2.0 / (period + 1)
    n_sym, n_t = values.shape
    out = np.full((n_sym, n_t), np.nan)
    prev = np.full(n_sym, np.nan)
    for t in range(n_t):
        v = values[:, t]
        has = ~np.isnan(v)
        seed = has & np.isnan(prev)
        cont = has & ~np.isnan(prev)
        prev[seed] = v[seed]
        prev[cont] = v[cont] * k + prev[cont] * (1.0 - k)
        out[:, t] = prev
    return out


def _load_window(con) -> dict:
    """Adjusted OHLCV matrices for the last WINDOW sessions."""
    daily_glob, _, actions_uri, _, _ = _uris()
    # Resolve the window's start date first so the heavy adjustment join only
    # ever touches the recent slice rather than the whole lake.
    start = con.execute(
        f"""
        SELECT MIN(date) FROM (
            SELECT DISTINCT date FROM read_parquet('{daily_glob}')
            ORDER BY date DESC LIMIT {WINDOW}
        )
        """
    ).fetchone()[0]

    cte = ca.adjusted_bars_cte(daily_glob, actions_uri, min_date=start.isoformat())
    tbl = con.execute(
        cte + "SELECT symbol, date, open, high, low, close, volume FROM bars_adj ORDER BY symbol, date"
    ).fetch_arrow_table()

    symbols = np.asarray(tbl.column("symbol").to_pylist(), dtype=object)
    dates = tbl.column("date").to_numpy(zero_copy_only=False).astype("datetime64[D]")

    uniq_sym, sym_idx = np.unique(symbols, return_inverse=True)
    uniq_date, date_idx = np.unique(dates, return_inverse=True)
    shape = (len(uniq_sym), len(uniq_date))

    def matrix(col: str) -> np.ndarray:
        m = np.full(shape, np.nan)
        m[sym_idx, date_idx] = tbl.column(col).to_numpy(zero_copy_only=False).astype(np.float64)
        return m

    return {
        "symbols": uniq_sym,
        "dates": uniq_date,
        "open": matrix("open"),
        "high": matrix("high"),
        "low": matrix("low"),
        "close": matrix("close"),
        "volume": matrix("volume"),
    }


def _load_aths(con) -> Dict[str, dict]:
    """Split-adjusted all-time high and first-listed date, over the full lake."""
    daily_glob, _, actions_uri, _, _ = _uris()
    cte = ca.adjusted_bars_cte(daily_glob, actions_uri)
    rows = con.execute(
        cte + """
        SELECT symbol,
               MAX(high)              AS ath_high,
               arg_max(date, high)    AS ath_date,
               MIN(date)              AS since
        FROM bars_adj
        WHERE high > 0
        GROUP BY symbol
        """
    ).fetchall()
    return {r[0]: {"high": r[1], "date": r[2].isoformat(), "since": r[3].isoformat()} for r in rows}


def _load_weekly(con) -> dict:
    """
    Split-adjusted weekly OHLCV for the last WEEKS weeks, as symbol-major matrices.

    Resampled in SQL rather than in numpy: the group-by is one streaming pass
    over a slice already partitioned by date, and it hands Python ~210 rows per
    symbol instead of the ~1,050 daily bars behind them. The current week is
    included while still partial — a breakout should be visible on the day it
    happens, not the following Monday.
    """
    daily_glob, _, actions_uri, _, _ = _uris()
    start = con.execute(
        f"""
        SELECT MIN(w) FROM (
            SELECT DISTINCT CAST(date_trunc('week', date) AS DATE) AS w
            FROM read_parquet('{daily_glob}')
            ORDER BY w DESC LIMIT {WEEKS}
        )
        """
    ).fetchone()[0]

    cte = ca.adjusted_bars_cte(daily_glob, actions_uri, min_date=start.isoformat())
    tbl = con.execute(
        cte + """
        SELECT symbol,
               CAST(date_trunc('week', date) AS DATE) AS wk,
               MAX(high)            AS high,
               arg_max(close, date) AS close,
               SUM(volume)          AS volume,
               SUM(traded_value)    AS turnover
        FROM bars_adj
        GROUP BY symbol, wk
        ORDER BY symbol, wk
        """
    ).fetch_arrow_table()

    symbols = np.asarray(tbl.column("symbol").to_pylist(), dtype=object)
    weeks = tbl.column("wk").to_numpy(zero_copy_only=False).astype("datetime64[D]")
    uniq_sym, sym_idx = np.unique(symbols, return_inverse=True)
    uniq_wk, wk_idx = np.unique(weeks, return_inverse=True)
    shape = (len(uniq_sym), len(uniq_wk))

    def matrix(col: str) -> np.ndarray:
        m = np.full(shape, np.nan)
        m[sym_idx, wk_idx] = tbl.column(col).to_numpy(zero_copy_only=False).astype(np.float64)
        return m

    return {
        "symbols": uniq_sym,
        "weeks": uniq_wk,
        "high": matrix("high"),
        "close": matrix("close"),
        "volume": matrix("volume"),
        "turnover": matrix("turnover"),
    }


def _recent_break(hv: np.ndarray, cv: np.ndarray, tv: np.ndarray, p: int, n: int):
    """
    Where the descending line anchored at pivot `p` has just given way.

    The tightest such line is the one whose slope is the running maximum of
    (high[k] - high[p]) / (t[k] - t[p]): at any week it sits on or above every
    high since the anchor, so it has demonstrably capped price for the whole span
    behind it. Taking the running maximum is what lets the search be one
    vectorised pass per anchor rather than a scan over every pair of pivots.

    Note what is deliberately *not* asked: whether this is the first time the
    anchor's line ever broke. An earlier, steeper draw of the same line can be
    pierced by a single 1% close and recover, and a trader would simply redraw
    it through the new lower high — so consuming the anchor on that first poke
    throws away exactly the multi-year lines worth having. What counts is a week
    that clears the line when the week before it did not, inside the window where
    that is still news.

    Returns (position of the break week, slope) or None.
    """
    dt = tv[p + 1:] - tv[p]
    rel = (hv[p + 1:] - hv[p]) / dt
    run = np.maximum.accumulate(rel)
    # The break week's own high must not help define the line it breaks, so each
    # candidate is judged against the slope the weeks before it had already set.
    slope = np.empty_like(run)
    slope[0] = np.nan
    slope[1:] = run[:-1]
    line = hv[p] + slope * dt
    with np.errstate(invalid="ignore"):
        clear = (slope < 0) & (dt >= MIN_SPAN) & (cv[p + 1:] > line * (1 + BREAK_MARGIN))
    # A break is a week that clears a line the previous week did not. Weeks two
    # and three of a hold are still above the line but are not the event.
    fresh = clear.copy()
    fresh[1:] &= ~clear[:-1]
    hit = np.flatnonzero(fresh & (tv[p + 1:] >= n - HOLD_WEEKS))
    if hit.size == 0:
        return None
    j = int(hit[0])
    return p + 1 + j, float(slope[j])


def _trend_breaks(wk: dict) -> Dict[str, dict]:
    """
    Per symbol, the descending weekly trendline that has just been broken.

    Where the 52-week and all-time tags ask "is price at an extreme right now",
    this asks "has a level that held for months stopped holding" — which is the
    case they structurally cannot see, since a stock clearing a two-year
    downtrend line is usually still well below both extremes.

    Two consequences follow from that difference and are deliberate. The flag
    persists for HOLD_WEEKS after the event instead of firing for one session,
    because a screener checked in the evening should not depend on being opened
    on exactly the right day. And it is dropped the moment price closes back
    under the line, because a break that is handed straight back is not one.

    Among the lines a symbol has broken, the winner is the one that capped price
    longest: a two-year downtrend giving way says more than a two-month one.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    high, close, vol, turn = wk["high"], wk["close"], wk["volume"], wk["turnover"]
    weeks = wk["weeks"]
    n_sym, n = high.shape
    out: Dict[str, dict] = {}
    if n < MIN_SPAN + 2 * K_SWING + 2:
        return out

    span = 2 * K_SWING + 1
    for r in range(n_sym):
        # Compact away the weeks this symbol did not trade, keeping the real week
        # index alongside: slopes are per week elapsed, so a gap must widen the
        # line's run rather than quietly shorten it.
        tv = np.flatnonzero(~np.isnan(high[r]))
        if tv.size < MIN_SPAN + span + 1 or tv[-1] < n - 2:
            continue
        hv, cv = high[r][tv], close[r][tv]
        if np.isnan(cv[-1]):
            continue

        win = sliding_window_view(hv, span)
        pivots = np.flatnonzero(hv[K_SWING:hv.size - K_SWING] == win.max(axis=1)) + K_SWING

        best = None
        for p in pivots:
            found = _recent_break(hv, cv, tv, int(p), n)
            if found is None:
                continue
            q, slope = found
            base, t0 = hv[p], tv[p]
            if cv[-1] <= base + slope * (tv[-1] - t0):
                continue  # broke, then handed the level straight back
            # A line needs more than the two points that draw it to be a line at
            # all: the anchor, the pivot whose slope it takes, and one more that
            # came down to it and turned.
            seen = pivots[(pivots >= p) & (pivots < q)]
            lvl = base + slope * (tv[seen] - t0)
            touches = int((np.abs(hv[seen] - lvl) <= TOUCH_TOL * lvl).sum())
            if touches < MIN_TOUCHES:
                continue
            if best is None or tv[q] - t0 > best[0]:
                best = (tv[q] - t0, q, touches, float(base + slope * (tv[q] - t0)))
        if best is None:
            continue

        held, q, touches, level = best
        vv = vol[r][tv]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # a fresh listing has no prior weeks
            base = np.nanmean(vv[max(0, q - 10):q])
        if np.nanmean(turn[r][tv][max(0, q - 10):q + 1]) < MIN_TURNOVER:
            continue
        out[str(wk["symbols"][r])] = {
            "trendBreak": True,
            "breakDate": str(weeks[tv[q]]),
            "breakWeeks": int(tv[-1] - tv[q]),
            "breakLevel": round(level, 2),
            "breakVol": round(float(vv[q] / base), 1) if base and base > 0 else None,
            "trendWeeks": int(held),
            "trendTouches": touches,
        }
    return out


def _load_sectors(con) -> Dict[str, str]:
    """
    symbol -> sector, from the ISIN-keyed industry dataset where it exists.

    `industry.parquet` labels ~81% of the lake against the NIFTY 500 file's 500
    names, which is the difference between a sector view that says "Other" for
    most of the market and one that does not. Joined on ISIN, so a company that
    changed ticker still resolves.

    Falls back to the constituent file when the industry dataset has not been
    built. The two vocabularies are never merged — blending them would split one
    sector across two labels and make the breadth numbers wrong.
    """
    daily, _, _, constituents, industry = _uris()
    try:
        rows = con.execute(
            f"""
            SELECT b.symbol, any_value(i.macro) AS sector
            FROM (SELECT DISTINCT symbol, isin FROM read_parquet('{daily}')
                  WHERE isin IS NOT NULL) b
            JOIN read_parquet('{industry}') i ON i.isin = b.isin
            WHERE i.macro IS NOT NULL
            GROUP BY b.symbol
            """
        ).fetchall()
        if rows:
            return {r[0]: r[1] for r in rows}
        print("[analytics] industry dataset empty; falling back to constituents")
    except Exception as err:  # noqa: BLE001 — not built yet; fall back
        print(f"[analytics] industry dataset unavailable ({err}); using constituents")

    try:
        rows = con.execute(
            f"""
            SELECT symbol, any_value(industry) AS industry
            FROM read_parquet('{constituents}')
            WHERE industry IS NOT NULL
            GROUP BY symbol
            """
        ).fetchall()
    except Exception as err:  # noqa: BLE001 — sector mapping is optional context
        print(f"[analytics] constituents unavailable ({err}); sectors will be 'Other'")
        return {}
    return {r[0]: r[1] for r in rows}


def _load_index_window(con, start: str) -> Dict[str, dict]:
    index_glob = _uris()[1]
    rows = con.execute(
        f"""
        SELECT index_name, date, open, high, low, close
        FROM read_parquet('{index_glob}')
        WHERE date >= DATE '{start}'
        ORDER BY index_name, date
        """
    ).fetchall()
    out: Dict[str, dict] = {}
    for name, d, o, h, lo, c in rows:
        out.setdefault(name, {"dates": [], "open": [], "high": [], "low": [], "close": []})
        e = out[name]
        e["dates"].append(d.isoformat())
        e["open"].append(o)
        e["high"].append(h)
        e["low"].append(lo)
        e["close"].append(c)
    return out


def _delivery_for(session: Date) -> Dict[str, float]:
    """
    Delivery percentages for the latest session, best effort.

    Prefers sec_bhavdata_full, which carries delivery alongside OHLCV and is
    reliably published; the MTO file is the fallback since its archive thins out.
    """
    for fetch_url, parse in (
        (nse.sec_bhavdata_url, lambda b: {k: v["delivery_pct"] for k, v in nse.parse_sec_bhavdata(b).items()}),
        (nse.mto_url, nse.parse_delivery),
    ):
        try:
            blob = nse.fetch(fetch_url(session))
            if blob:
                return parse(blob)
        except Exception:  # noqa: BLE001 — delivery is display context, not core data
            continue
    return {}


def compute(con=None) -> dict:
    own = con is None
    con = con or r2.duck()
    try:
        w = _load_window(con)
        dates = w["dates"]
        n_t = len(dates)
        if n_t < 30:
            raise RuntimeError(f"only {n_t} sessions in the lake — backfill first")

        close, high, low, vol = w["close"], w["high"], w["low"], w["volume"]
        symbols = w["symbols"]
        last = n_t - 1
        latest = str(dates[last])

        aths = _load_aths(con)
        sector_of = _load_sectors(con)
        indices = _load_index_window(con, str(dates[max(0, n_t - 260)]))
        breaks = _trend_breaks(_load_weekly(con))

        e10 = _ema_matrix(close, 10)
        e20 = _ema_matrix(close, 20)
        e50 = _ema_matrix(close, 50)
        e200 = _ema_matrix(close, 200)

        hist_start = n_t - HIST
        prev_close = np.full_like(close, np.nan)
        prev_close[:, 1:] = close[:, :-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            chg = (close - prev_close) / prev_close * 100.0

        valid = ~np.isnan(close) & ~np.isnan(prev_close) & (prev_close > 0)

        # ── Breadth counters over the published history ──────────────────────
        agg = {k: np.zeros(HIST, dtype=np.int64) for k in (
            "advances", "declines", "unchanged", "newHighs", "newLows", "athCount",
            "above10", "above20", "above50", "above200", "counted",
            "up20", "up30", "volUp4", "volDn4",
        )}

        for j in range(HIST):
            t = hist_start + j
            v = valid[:, t]
            agg["counted"][j] = int(v.sum())
            c = chg[:, t]
            agg["advances"][j] = int((v & (c > 0.0001)).sum())
            agg["declines"][j] = int((v & (c < -0.0001)).sum())
            agg["unchanged"][j] = int((v & (np.abs(c) <= 0.0001)).sum())

            lo_t = max(0, t - 251)
            # A symbol delisted before this window, or listed after it, has an
            # all-NaN row here. nanmax warns and returns NaN, which is exactly
            # the answer we want — the `valid` mask excludes it either way.
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                hi52 = np.nanmax(high[:, lo_t:t + 1], axis=1)
                lo52 = np.nanmin(low[:, lo_t:t + 1], axis=1)
            agg["newHighs"][j] = int((v & (hi52 > 0) & (high[:, t] >= hi52)).sum())
            agg["newLows"][j] = int((v & np.isfinite(lo52) & (low[:, t] <= lo52)).sum())

            for key, ema in (("above10", e10), ("above20", e20), ("above50", e50), ("above200", e200)):
                agg[key][j] = int((v & ~np.isnan(ema[:, t]) & (close[:, t] > ema[:, t])).sum())

            if t >= 5:
                base = close[:, t - 5]
                with np.errstate(invalid="ignore", divide="ignore"):
                    r5 = (close[:, t] / base - 1.0) * 100.0
                ok = v & ~np.isnan(base) & (base > 0)
                agg["up20"][j] = int((ok & (r5 >= 20)).sum())
                agg["up30"][j] = int((ok & (r5 >= 30)).sum())

            if t >= 20:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)  # empty slice → NaN, masked below
                    sma = np.nanmean(vol[:, t - 20:t], axis=1)
                surge = v & (sma > 0) & (vol[:, t] > sma)
                agg["volUp4"][j] = int((surge & (c >= 4)).sum())
                agg["volDn4"][j] = int((surge & (c <= -4)).sum())

        # ── Latest-session per-symbol metrics ────────────────────────────────
        active_mask = ~np.isnan(close[:, last]) & (close[:, last] > 0)
        active = np.flatnonzero(active_mask)

        lo_t = max(0, last - 251)
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows are delisted symbols
            hi52_last = np.nanmax(high[:, lo_t:last + 1], axis=1)

        bench = None
        for name in ("NIFTY 500", "NIFTY 50"):
            if name in indices and len(indices[name]["close"]) > 64:
                bench = indices[name]["close"]
                break
        bench_ret63 = bench[-1] / bench[-64] if bench and bench[-64] > 0 else None

        delivery = _delivery_for(dates[last].astype(object))

        stocks: List[dict] = []
        ath_count = 0
        for i in active:
            sym = str(symbols[i])
            price = float(close[i, last])
            p1 = close[i, last - 1]
            chg1d = float((price / p1 - 1) * 100) if last >= 1 and not np.isnan(p1) and p1 > 0 else 0.0
            p5 = close[i, last - 5] if last >= 5 else np.nan
            chg1w = float((price / p5 - 1) * 100) if not np.isnan(p5) and p5 > 0 else 0.0

            rec = aths.get(sym)
            ath_high = max(rec["high"] if rec else 0.0, price)  # a new high today *is* the ATH
            dist_ath = max(0.0, (ath_high - price) / ath_high * 100) if ath_high > 0 else 0.0
            hi52 = hi52_last[i]
            dist52 = max(0.0, float((hi52 - price) / hi52 * 100)) if hi52 > 0 else 0.0

            is_ath = dist_ath < 0.4
            is52 = dist52 < 0.5
            if is_ath:
                ath_count += 1

            rs = 0.0
            if bench_ret63 and last >= 63:
                base = close[i, last - 63]
                if not np.isnan(base) and base > 0:
                    rs = float((price / base) / bench_ret63 * 100 - 100)

            row = {
                "sym": sym,
                "sector": sector_of.get(sym, "Other"),
                "price": price,
                "chg1d": chg1d,
                "chg1w": chg1w,
                "distATH": dist_ath,
                "dist52": dist52,
                "isATH": is_ath,
                "is52": is52,
                "wkBreak": (not is52) and chg1w > 3.5 and dist52 < 6,
                "athSince": rec["since"] if rec else None,
                "athDate": rec["date"] if rec else None,
                "e10": _f(e10[i, last]), "e20": _f(e20[i, last]),
                "e50": _f(e50[i, last]), "e200": _f(e200[i, last]),
                "rs": rs,
                "volume": _f(vol[i, last]) or 0.0,
                "deliveryPct": delivery.get(sym),
                "turnover": (_f(vol[i, last]) or 0.0) * price,
                "trendBreak": False,
            }
            # Present only on the symbols that have one, so the ~2,800 rows that
            # do not stay the size they already were in the published payload.
            row.update(breaks.get(sym, {}))
            stocks.append(row)
        agg["athCount"][HIST - 1] = ath_count

        sectors = _sector_scores(stocks)
        candles = _candle_payload(w, active)

        # int() is redundant today — round() returns a Python int for every numpy
        # dtype here — but this value is JSON-serialized into Postgres, and numpy
        # scalars are not serializable. Keeping the cast makes that guarantee
        # explicit rather than inherited from round()'s return type.
        pct = lambda arr, j: int(round(arr[j] / agg["counted"][j] * 100)) if agg["counted"][j] else 0  # noqa: E731, RUF046
        tail = lambda arr, n: [int(x) for x in arr[HIST - min(n, HIST):]]  # noqa: E731
        j = HIST - 1
        hist_dates = [str(d) for d in dates[hist_start:]]

        breadth = {
            "date": latest,
            "universe": len(active),
            "advances": int(agg["advances"][j]),
            "declines": int(agg["declines"][j]),
            "unchanged": int(agg["unchanged"][j]),
            "newHighs": int(agg["newHighs"][j]),
            "newLows": int(agg["newLows"][j]),
            "athCount": int(agg["athCount"][j]),
            "emaVals": {"e10": pct(agg["above10"], j), "e20": pct(agg["above20"], j),
                        "e50": pct(agg["above50"], j), "e200": pct(agg["above200"], j)},
            "emaHist": {
                "e20": [pct(agg["above20"], i) for i in range(HIST)],
                "e50": [pct(agg["above50"], i) for i in range(HIST)],
                "e200": [pct(agg["above200"], i) for i in range(HIST)],
            },
            "adDaily": [int(agg["advances"][t] - agg["declines"][t]) for t in range(max(0, HIST - 90), HIST)],
            "nhDaily": [int(agg["newHighs"][t] - agg["newLows"][t]) for t in range(max(0, HIST - 90), HIST)],
            "series": {
                "newHighs": tail(agg["newHighs"], 45), "newLows": tail(agg["newLows"], 45),
                "up20": tail(agg["up20"], 45), "up30": tail(agg["up30"], 45),
                "up4vol": tail(agg["volUp4"], 45), "down4vol": tail(agg["volDn4"], 45),
                "netHL": [a - b for a, b in zip(tail(agg["newHighs"], 45), tail(agg["newLows"], 45), strict=True)],
            },
            "dates": hist_dates,
        }

        return {
            "date": latest,
            "breadth": breadth,
            "sectors": sectors,
            "stocks": stocks,
            "indices": indices,
            "candles": candles,
        }
    finally:
        if own:
            con.close()


def _f(x) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def _sector_scores(stocks: List[dict]) -> List[dict]:
    by: Dict[str, List[dict]] = {}
    for s in stocks:
        if s["sector"] == "Other":
            continue
        by.setdefault(s["sector"], []).append(s)

    out = []
    for name, lst in by.items():
        count = len(lst)
        adv = sum(1 for x in lst if x["chg1d"] > 0)
        above50 = sum(1 for x in lst if x["e50"] is not None and x["price"] > x["e50"])
        dma_pct = round(above50 / count * 100)
        new_highs = sum(1 for x in lst if x["is52"])
        wk = sum(x["chg1w"] for x in lst) / count
        score = max(2, min(99, round(
            dma_pct * 0.5 + max(0, min(30, (wk + 3) * 5)) + min(20, new_highs / count * 220)
        )))
        out.append({"name": name, "count": count, "adv": adv, "dec": count - adv,
                    "dmaPct": dma_pct, "newHighs": new_highs, "wk": wk, "score": score})
    return sorted(out, key=lambda x: -x["score"])


def _candle_payload(w: dict, active: np.ndarray) -> Dict[str, dict]:
    """
    Last CANDLES adjusted bars per active symbol, stored columnar.

    Columnar arrays rather than an array of objects: same data, roughly a third
    of the JSON bytes, which matters when this is ~2,400 symbols in a 500 MB
    Supabase tier.
    """
    dates = [str(d) for d in w["dates"]]
    out: Dict[str, dict] = {}
    for i in active:
        sym = str(w["symbols"][i])
        c = w["close"][i]
        have = np.flatnonzero(~np.isnan(c))
        if have.size == 0:
            continue
        sel = have[-CANDLES:]
        out[sym] = {
            "d": [dates[t] for t in sel],
            "o": [round(float(w["open"][i, t]), 2) for t in sel],
            "h": [round(float(w["high"][i, t]), 2) for t in sel],
            "l": [round(float(w["low"][i, t]), 2) for t in sel],
            "c": [round(float(w["close"][i, t]), 2) for t in sel],
            "v": [int(w["volume"][i, t]) if not np.isnan(w["volume"][i, t]) else 0 for t in sel],
        }
    return out
