"""
Loading `MarketData` — the one place that knows where bars come from.

Two sources, same shape out:

  from_lake()     DuckDB over the R2 Parquet, what the nightly job uses
  from_parquet()  a local mirror, for the backtest and for fast iteration

Keeping both behind one return type is what lets `rules.py` stay free of I/O,
and lets the backtest run against exactly the bars the live job sees.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyarrow.parquet as pq

from ...config import CURATED_DAILY, s3_uri
from ...ingest import corporate_actions as ca
from ...ingest.reference import CONSTITUENTS_KEY
from .rules import MarketData

# Columns every source must provide, in the order the matrices are built.
_COLS = ("open", "high", "low", "close", "volume", "traded_value", "raw_close")


def _assemble(symbols, dates, cols: dict, series, sector_map: Optional[dict]) -> MarketData:
    """Long-form columns -> [dates x symbols] matrices."""
    usym, si = np.unique(symbols, return_inverse=True)
    udate, di = np.unique(dates, return_inverse=True)
    shape = (len(udate), len(usym))

    def mat(values, dtype=np.float32):
        m = np.full(shape, np.nan, dtype=dtype)
        m[di, si] = np.asarray(values, dtype=dtype)
        return m

    is_eq = np.zeros(shape, bool)
    is_eq[di, si] = np.asarray([s == "EQ" for s in series])

    sector = None
    if sector_map:
        sector = np.array([sector_map.get(str(s)) for s in usym], dtype=object)

    return MarketData(
        symbols=usym, dates=udate,
        open=mat(cols["open"]), high=mat(cols["high"]), low=mat(cols["low"]),
        close=mat(cols["close"]), volume=mat(cols["volume"]),
        turnover=mat(cols["traded_value"], np.float64),
        raw_close=mat(cols["raw_close"]),
        is_eq=is_eq, sector=sector,
    )


def from_lake(con, start: Optional[str] = None, sectors: bool = True) -> MarketData:
    """
    Split-adjusted bars from R2, optionally from `start` onward.

    The live job passes a start date: the rules need only
    `cfg.lookback_needed` sessions of history, and scanning 19 years nightly to
    compute a 250-day high would be wasteful.

    `raw_close` comes from the unadjusted table on purpose — the penny-stock
    floor must see the price as it actually traded, or a pre-split bar reads as
    ₹100 for something that changed hands at ₹1,000.
    """
    daily = s3_uri(f"{CURATED_DAILY}/*/data.parquet")
    actions = s3_uri(ca.ACTIONS_KEY)
    cte = ca.adjusted_bars_cte(daily, actions, min_date=start)
    tbl = con.execute(cte + f"""
        SELECT a.symbol, a.date, a.open, a.high, a.low, a.close, a.volume,
               a.traded_value, r.close AS raw_close, r.series
        FROM bars_adj a
        JOIN read_parquet('{daily}') r ON r.symbol = a.symbol AND r.date = a.date
        ORDER BY a.symbol, a.date
    """).fetch_arrow_table()

    sector_map = _sector_map(con) if sectors else None
    return _assemble(
        np.asarray(tbl.column("symbol").to_pylist(), dtype=object),
        tbl.column("date").to_numpy(zero_copy_only=False).astype("datetime64[D]"),
        {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in _COLS},
        tbl.column("series").to_pylist(),
        sector_map,
    )


def _sector_map(con) -> dict:
    """
    symbol -> industry, from the constituent file.

    This is today's NIFTY 500 membership — the only sector map in the lake — so
    it is a present-day label applied to history. The control run (same
    universe, sector selection off) shows the overlay's gain is a real rotation
    effect rather than survivorship, but live results should be discounted a
    little against the backtest for it.
    """
    rows = con.execute(
        f"SELECT symbol, industry FROM read_parquet('{s3_uri(CONSTITUENTS_KEY)}') "
        "WHERE industry IS NOT NULL"
    ).fetchall()
    out: dict = {}
    for sym, industry in rows:
        out.setdefault(sym, industry)
    return out


def from_parquet(bars_path: str, constituents_path: Optional[str] = None) -> MarketData:
    """A local mirror of the same columns; used by the backtest harness."""
    tbl = pq.read_table(bars_path)
    sector_map: Optional[dict] = None
    if constituents_path:
        ct = pq.read_table(constituents_path)
        sector_map = {}
        for sym, industry in zip(ct.column("symbol").to_pylist(),
                                 ct.column("industry").to_pylist(), strict=True):
            if industry:
                sector_map.setdefault(sym, industry)
    return _assemble(
        np.asarray(tbl.column("symbol").to_pylist(), dtype=object),
        tbl.column("date").to_numpy(zero_copy_only=False).astype("datetime64[D]"),
        {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in _COLS},
        tbl.column("series").to_pylist(),
        sector_map,
    )
