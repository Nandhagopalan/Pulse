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
from ...ingest.industry import INDUSTRY_URI
from ...ingest.reference import CONSTITUENTS_KEY
from .rules import MarketData

# Columns every source must provide, in the order the matrices are built.
_COLS = ("open", "high", "low", "close", "volume", "traded_value", "raw_close")

# What a fund unit tracks, matched against the ticker in this order; the first
# hit wins. NSE gives no machine-readable answer to "what is the underlying",
# and the tickers are the only signal available — so this is a heuristic, and
# deliberately a coarse one. It exists to stop the book holding seven silver
# ETFs as though they were seven positions, not to classify the whole market.
# Over-grouping is the safe failure: it declines a trade rather than doubling a
# bet the risk engine cannot see.
_TRACKS = (
    ("silver", ("SILVER", "SILVRETF", "TATSILV", "ESILVER", "MASILVER")),
    ("gold",   ("GOLD", "EGOLD", "IGOLD", "MGOLD", "QGOLDHALF")),
    ("liquid", ("LIQUID", "LIQGRW", "CASHIETF", "AONELIQ", "ELIQUID")),
    ("debt",   ("GILT", "GSEC", "SDL", "GS813", "BND", "BBETF", "EBBETF",
                "IPGETF", "NCPSESDL", "ABGSEC", "SBIGETS", "G5", "5GSEC", "10GS")),
    ("bank",   ("BANK", "BNK", "BFSI", "PSUBK", "PVTBK", "FINIETF", "ECAPINSURE",
                "INSUREIETF")),
    ("tech",   ("TECH", "ITETF", "ITBEES", "ITIETF", "ITADD", "ITAXIS", "ITBETA",
                "NIFIT", "ETFIT", "DSPIT", "KOTAKIT", "NETFIT", "NIFITETF",
                "INTERNET", "FANG", "MON100", "N100", "MONQ50", "NQ")),
    ("pharma", ("PHARMA", "HEALTH")),
)


def _classify(symbol: str, isin: Optional[str]) -> tuple:
    """
    (is_equity, tracked-underlying) for one ticker.

    NSE lists ETF and fund units in the EQ series alongside companies, so the
    series column cannot separate them. The ISIN can: INE is an equity issue,
    INF a mutual-fund unit. A missing ISIN is treated as equity — the gap is in
    the oldest bars, long-delisted companies from before the field was carried,
    and calling those funds would quietly drop them from history.
    """
    if not isin or not str(isin).startswith(("INF", "IN9")):
        return True, None
    up = str(symbol).upper()
    for name, keys in _TRACKS:
        if any(k in up for k in keys):
            return False, name
    # An unclassified fund is grouped under its own ticker rather than lumped
    # with every other unclassified one: the cap should stop duplicates, not
    # stop the book holding two funds that happen to track different things.
    return False, f"fund:{up}"


def _assemble(symbols, dates, cols: dict, series, sector_map: Optional[dict],
              isin_map: Optional[dict] = None) -> MarketData:
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

    is_equity = None
    group = None
    if isin_map is not None:
        flags, tracks = [], []
        for sym in usym:
            eq, track = _classify(str(sym), isin_map.get(str(sym)))
            flags.append(eq)
            tracks.append(track)
        is_equity = np.asarray(flags, dtype=bool)
        group = np.array(tracks, dtype=object)

    return MarketData(
        symbols=usym, dates=udate,
        open=mat(cols["open"]), high=mat(cols["high"]), low=mat(cols["low"]),
        close=mat(cols["close"]), volume=mat(cols["volume"]),
        turnover=mat(cols["traded_value"], np.float64),
        raw_close=mat(cols["raw_close"]),
        is_eq=is_eq, sector=sector, is_equity=is_equity, group=group,
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
               a.traded_value, r.close AS raw_close, r.series, r.isin
        FROM bars_adj a
        JOIN read_parquet('{daily}') r ON r.symbol = a.symbol AND r.date = a.date
        ORDER BY a.symbol, a.date
    """).fetch_arrow_table()

    isin_map = _isin_map(np.asarray(tbl.column("symbol").to_pylist(), dtype=object),
                         tbl.column("isin").to_pylist())
    sector_map = _sector_map(con, isin_map) if sectors else None
    syms = np.asarray(tbl.column("symbol").to_pylist(), dtype=object)
    return _assemble(
        syms,
        tbl.column("date").to_numpy(zero_copy_only=False).astype("datetime64[D]"),
        {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in _COLS},
        tbl.column("series").to_pylist(),
        sector_map,
        isin_map,
    )


def _isin_map(symbols, isins) -> dict:
    """symbol -> ISIN, taking the first non-null seen for each ticker."""
    out: dict = {}
    for sym, isin in zip(symbols, isins, strict=True):
        if isin and str(sym) not in out:
            out[str(sym)] = isin
    return out


def _sector_map(con, isin_map: Optional[dict] = None) -> dict:
    """
    symbol -> macro sector.

    Preferred source is `industry.parquet`, keyed by ISIN and built from BSE's
    scrip master, which retains delisted companies: it labels ~81% of the lake's
    equities and the same ~81% of the delisted ones. What it misses does not
    correlate with survival, which is the property that matters — a filter built
    on labels that only exist for survivors is a survivorship screen.

    The fallback is the NIFTY 500 constituent file. That covers 500 names, all
    of them currently listed, so a rule that excludes what it cannot classify
    silently becomes "hold only today's index members". Kept only so the engine
    still runs before the industry dataset has been built for the first time.

    The two are never mixed: they are different vocabularies, and blending them
    would split one real sector across two names.
    """
    if isin_map:
        try:
            rows = con.execute(
                f"SELECT isin, macro FROM read_parquet('{INDUSTRY_URI}') "
                "WHERE macro IS NOT NULL"
            ).fetchall()
        except Exception:  # noqa: BLE001 — not built yet; fall through
            rows = []
        if rows:
            by_isin = dict(rows)
            out: dict = {}
            for sym, isin in isin_map.items():
                macro = by_isin.get(isin)
                if macro:
                    out[sym] = macro
            if out:
                return out

    rows = con.execute(
        f"SELECT symbol, industry FROM read_parquet('{s3_uri(CONSTITUENTS_KEY)}') "
        "WHERE industry IS NOT NULL"
    ).fetchall()
    out = {}
    for sym, industry in rows:
        out.setdefault(sym, industry)
    return out


def from_parquet(bars_path: str, constituents_path: Optional[str] = None,
                 industry_path: Optional[str] = None) -> MarketData:
    """
    A local mirror of the same columns; used by the backtest harness.

    `industry_path` is the local copy of `industry.parquet`. When given it wins
    over the constituent file, for the reasons in `_sector_map`.
    """
    tbl = pq.read_table(bars_path)
    sector_map: Optional[dict] = None
    isin_map: Optional[dict] = None
    if constituents_path:
        ct = pq.read_table(constituents_path)
        sector_map = {}
        for sym, industry in zip(ct.column("symbol").to_pylist(),
                                 ct.column("industry").to_pylist(), strict=True):
            if industry:
                sector_map.setdefault(sym, industry)
    syms = np.asarray(tbl.column("symbol").to_pylist(), dtype=object)
    if "isin" in tbl.schema.names:
        isin_map = _isin_map(syms, tbl.column("isin").to_pylist())
    if industry_path and isin_map:
        it = pq.read_table(industry_path)
        by_isin = {i: m for i, m in zip(it.column("isin").to_pylist(),
                                        it.column("macro").to_pylist(), strict=True) if m}
        mapped = {sym: by_isin[isin] for sym, isin in isin_map.items() if isin in by_isin}
        if mapped:
            sector_map = mapped
    return _assemble(
        syms,
        tbl.column("date").to_numpy(zero_copy_only=False).astype("datetime64[D]"),
        {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in _COLS},
        tbl.column("series").to_pylist(),
        sector_map,
        isin_map,
    )
