"""
Index derivatives lake: NSE F&O bhavcopy -> a dedicated R2 bucket.

Kept apart from the terminal's lake on purpose. It lives in its own bucket
(FNO_R2_BUCKET, or --bucket), and nothing here can fall back to pulse-terminal:
`config.require_fno_bucket` refuses an unset name and refuses the terminal's.

Layers, keyed like the terminal's lake:

  raw/nse/fo_bhavcopy/YYYY/YYYY-MM-DD.zip              vendor bytes, every F&O contract
  raw/nse/fovolt/YYYY/YYYY-MM-DD.csv                   clearing-house volatility file
  curated/index_fno_daily/year=YYYY/data.parquet       index futures + options, chosen symbols
  curated/index_underlying_daily/year=YYYY/data.parquet  one spot close per symbol per session

The raw layer is kept whole (every symbol) so another index can be curated later
without asking NSE again. Curated files hold only the index symbols requested.

Spot comes from FOVOLT, whose files carry the underlying's close from April 2011.
For sessions before that, or a day its file is missing, the nearest unexpired
future's close stands in and the row says so in `source`, so a backtest can
exclude proxy days instead of trusting them silently.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from time import sleep
from typing import Iterator, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import (
    CURATED_FNO_CONTRACTS,
    CURATED_FNO_UNDERLYING,
    RAW_FO_BHAV_PREFIX,
    RAW_FO_MARKET_ACTIVITY_PREFIX,
    RAW_FOVOLT_PREFIX,
    config,
    s3_uri,
)
from ..sources import nse, r2

DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY")

CONTRACT_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("date", pa.date32()),
    ("instrument", pa.string()),       # FUT | OPT
    ("expiry", pa.date32()),
    ("strike", pa.float64()),          # null for futures
    ("option_type", pa.string()),      # CE | PE, null for futures
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),           # the price to use; settlement value even when untraded
    ("settle", pa.float64()),          # as published; see nse.py on legacy option rows
    ("prev_close", pa.float64()),      # UDiFF only
    ("underlying", pa.float64()),      # UDiFF only
    ("contracts", pa.int64()),         # volume, in contracts
    ("oi", pa.int64()),                # open interest, in units
    ("chg_oi", pa.int64()),            # in units
    ("turnover", pa.float64()),        # rupees, notional
    ("trades", pa.int64()),            # UDiFF only
    ("lot_size", pa.int64()),          # published (UDiFF) or recovered from turnover (legacy)
])

UNDERLYING_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("date", pa.date32()),
    ("close", pa.float64()),
    ("prev_close", pa.float64()),
    ("underlying_vol", pa.float64()),  # annualised, from FOVOLT
    ("futures_close", pa.float64()),
    ("source", pa.string()),           # fovolt | near_future
])

LOCAL_ROOT: Optional[Path] = None
USE_R2 = True
BUCKET = ""


def configure(bucket: Optional[str] = None, local: Optional[str] = None, with_r2: bool = True) -> None:
    global LOCAL_ROOT, USE_R2, BUCKET
    LOCAL_ROOT = Path(local).expanduser().resolve() if local else None
    USE_R2 = with_r2
    if not USE_R2 and LOCAL_ROOT is None:
        raise RuntimeError("--no-r2 needs --local DIR; there would be nowhere to write")
    BUCKET = config.require_fno_bucket(bucket or "") if USE_R2 else (bucket or "")


def _local(key: str) -> Optional[Path]:
    return LOCAL_ROOT / key if LOCAL_ROOT else None


def _get(key: str) -> Optional[bytes]:
    p = _local(key)
    if p is not None and p.exists():
        return p.read_bytes()
    return r2.get_object(key, bucket=BUCKET) if USE_R2 else None


def _put(key: str, blob: bytes, content_type: str) -> None:
    p = _local(key)
    if p is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    if USE_R2:
        r2.put_object(key, blob, content_type=content_type, bucket=BUCKET)


def _exists(key: str) -> bool:
    p = _local(key)
    if p is not None and p.exists():
        return True
    return r2.object_exists(key, bucket=BUCKET) if USE_R2 else False


def raw_fo_key(d: date) -> str:
    return f"{RAW_FO_BHAV_PREFIX}/{d.year}/{d.isoformat()}.zip"


def raw_fo_market_activity_key(d: date) -> str:
    return f"{RAW_FO_MARKET_ACTIVITY_PREFIX}/{d.year}/{d.isoformat()}.zip"


def raw_fovolt_key(d: date) -> str:
    return f"{RAW_FOVOLT_PREFIX}/{d.year}/{d.isoformat()}.csv"


def contracts_key(year: int) -> str:
    return f"{CURATED_FNO_CONTRACTS}/year={year}/data.parquet"


def underlying_key(year: int) -> str:
    return f"{CURATED_FNO_UNDERLYING}/year={year}/data.parquet"


def _weekdays(year: int, start: date, end: date) -> Iterator[date]:
    d = max(date(year, 1, 1), start)
    stop = min(date(year, 12, 31), end)
    while d <= stop:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _fetch_cached(key: str, url: str) -> Tuple[Optional[bytes], bool]:
    """(bytes, came_from_nse). None means NSE has no such file: a holiday, or not published."""
    cached = _get(key)
    if cached is not None:
        return cached, False
    blob = nse.fetch(url)
    sleep(config.nse_delay)
    return blob, blob is not None


def _write(key: str, tables: List[pa.Table], schema: pa.Schema, sort: List[Tuple[str, str]]) -> int:
    if not tables:
        return 0
    table = pa.concat_tables(tables).sort_by(sort)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", compression_level=6)
    _put(key, buf.getvalue(), "application/vnd.apache.parquet")
    return table.num_rows


def fill_untraded_lots(table: pa.Table) -> pa.Table:
    """
    Give every contract row a lot size, including sessions the contract did not trade.

    Legacy files publish no lot, and `nse._fill_legacy_lots` can only estimate one
    for an expiry that traded that session. A contract keeps its lot for life
    apart from an NSE revision, so an untraded day takes the same contract's lot
    from its nearest earlier traded session, else its next one. Only a contract
    that never trades in the year falls back to that session's most common lot.
    """
    if table.num_rows == 0 or table.column("lot_size").null_count == 0:
        return table
    import duckdb
    con = duckdb.connect()
    con.register("contracts", table)
    filled = con.execute("""
        SELECT * REPLACE (COALESCE(
            lot_size,
            LAST_VALUE(lot_size IGNORE NULLS) OVER before_row,
            FIRST_VALUE(lot_size IGNORE NULLS) OVER after_row,
            MODE(lot_size) OVER (PARTITION BY symbol, date)
        ) AS lot_size)
        FROM contracts
        WINDOW before_row AS (PARTITION BY symbol, expiry ORDER BY date
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
               after_row AS (PARTITION BY symbol, expiry ORDER BY date
                             ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
    """).to_arrow_table()
    return filled.cast(CONTRACT_SCHEMA)


def ingest_year(year: int, start: date, end: date, symbols: Sequence[str],
                force: bool = False) -> Tuple[int, int]:
    """One calendar year. Returns (contract rows, underlying rows) written."""
    # A window that doesn't cover the whole year (the current year, or a sample)
    # always rebuilds, and the curated file then holds only that window's sessions.
    partial = start > date(year, 1, 1) or end < date(year, 12, 31)
    curated = _exists(contracts_key(year)) and _exists(underlying_key(year))
    if curated and not force and not partial:
        print(f"[fno] {year}: curated files present - skipping (use --force to rebuild)")
        return 0, 0
    if curated and partial:
        print(f"[fno] {year}: rebuilding the curated file from {max(start, date(year, 1, 1))} "
              f"to {min(end, date(year, 12, 31))} only", flush=True)

    contract_tables: List[pa.Table] = []
    spot_tables: List[pa.Table] = []
    failed: List[date] = []
    sessions = holidays = fetched = proxy_days = 0
    fallback: List[date] = []

    for d in _weekdays(year, start, end):
        # One bad file out of ~250 must not discard the year. Failures are listed at
        # the end; re-running retries exactly those, since everything else is cached.
        try:
            blob, from_nse = _fetch_cached(raw_fo_key(d), nse.fo_bhav_url(d))
            if blob is not None:
                if from_nse:
                    _put(raw_fo_key(d), blob, "application/zip")
                    fetched += 1
                rows = nse.parse_fo_bhavcopy(blob, d, symbols)
            else:
                # No bhavcopy is usually a holiday, but NSE also skipped it for a few
                # sessions that traded. The Market Activity zip exists for those and
                # not for holidays, so its absence is what makes a day a non-session.
                mblob, m_from_nse = _fetch_cached(raw_fo_market_activity_key(d), nse.fo_market_activity_url(d))
                if mblob is None:
                    holidays += 1
                    continue
                if m_from_nse:
                    _put(raw_fo_market_activity_key(d), mblob, "application/zip")
                    fetched += 1
                rows = nse.parse_fo_market_activity(mblob, d, symbols)
                fallback.append(d)
        except Exception as err:  # noqa: BLE001 - collected and reported, the year carries on
            print(f"[fno] {d}: F&O bhavcopy FAILED: {err}", flush=True)
            failed.append(d)
            continue
        sessions += 1
        if rows:
            contract_tables.append(pa.Table.from_pylist(rows, schema=CONTRACT_SCHEMA))

        spot: List[dict] = []
        try:
            vblob, v_from_nse = _fetch_cached(raw_fovolt_key(d), nse.fovolt_url(d))
            if vblob is not None:
                if v_from_nse:
                    _put(raw_fovolt_key(d), vblob, "text/csv")
                spot = nse.parse_fovolt(vblob, d, symbols)
        except Exception as err:  # noqa: BLE001 - spot falls back to the near future below
            print(f"[fno] {d}: FOVOLT failed ({err}) - using near-future closes", flush=True)
        have = {s["symbol"] for s in spot}
        for sym in symbols:
            if sym in have:
                continue
            proxy = nse.near_future_close(rows, sym, d)
            if proxy is not None:
                spot.append(proxy)
                proxy_days += 1
        if spot:
            spot_tables.append(pa.Table.from_pylist(spot, schema=UNDERLYING_SCHEMA))

        if sessions % 25 == 0:
            print(f"[fno] {year}: {sessions} sessions ...", flush=True)

    if contract_tables:
        contract_tables = [fill_untraded_lots(pa.concat_tables(contract_tables))]
    n_contracts = _write(contracts_key(year), contract_tables, CONTRACT_SCHEMA,
                         [("date", "ascending"), ("symbol", "ascending"), ("expiry", "ascending"),
                          ("instrument", "ascending"), ("strike", "ascending"),
                          ("option_type", "ascending")])
    n_spot = _write(underlying_key(year), spot_tables, UNDERLYING_SCHEMA,
                    [("date", "ascending"), ("symbol", "ascending")])
    print(f"[fno] {year}: {sessions} sessions ({fetched} from NSE), {holidays} non-sessions, "
          f"{n_contracts:,} contract rows, {n_spot:,} spot rows ({proxy_days} near-future proxies)",
          flush=True)
    if fallback:
        print(f"[fno] {year}: {len(fallback)} session(s) had no bhavcopy and came from the Market Activity "
              f"report (traded contracts only): {', '.join(str(d) for d in fallback)}", flush=True)
    if failed:
        print(f"[fno] {year}: {len(failed)} session(s) FAILED and are missing: "
              f"{', '.join(str(d) for d in failed)}", flush=True)
    return n_contracts, n_spot


def run(start: date, end: date, symbols: Sequence[str] = DEFAULT_SYMBOLS, force: bool = False) -> None:
    if USE_R2:
        r2.preflight(BUCKET)
    dest = f"r2://{BUCKET}" if USE_R2 else str(LOCAL_ROOT)
    print(f"[fno] {start} -> {end}, symbols {', '.join(symbols)}, into {dest}")
    total_c = total_s = 0
    for year in range(start.year, end.year + 1):
        c, s = ingest_year(year, start, end, symbols, force=force)
        total_c += c
        total_s += s
    print(f"[fno] done: {total_c:,} contract rows, {total_s:,} spot rows")


def _glob(prefix: str) -> str:
    if not USE_R2 and LOCAL_ROOT is not None:
        return str(LOCAL_ROOT / prefix / "*" / "data.parquet")
    return s3_uri(f"{prefix}/*/data.parquet", bucket=BUCKET)


def summary() -> None:
    """What the F&O lake holds, per year and symbol - the post-ingest sanity check."""
    import duckdb
    con = r2.duck() if USE_R2 else duckdb.connect()
    rows = con.execute(f"""
        SELECT year(date) AS y, symbol, COUNT(DISTINCT date) AS sessions, COUNT(*) AS n,
               COUNT(DISTINCT expiry) FILTER (WHERE instrument = 'OPT') AS expiries,
               string_agg(DISTINCT CAST(lot_size AS VARCHAR), '/') AS lots,
               MIN(date) AS d0, MAX(date) AS d1
        FROM read_parquet('{_glob(CURATED_FNO_CONTRACTS)}')
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    print(f"{'year':>4} {'symbol':10} {'sessions':>8} {'rows':>10} {'expiries':>8}  lots        range")
    for y, sym, ses, n, exp, lots, d0, d1 in rows:
        print(f"{y:>4} {sym:10} {ses:>8} {n:>10,} {exp:>8}  {lots or '-':10}  {d0} -> {d1}")
    spot = con.execute(f"""
        SELECT year(date) AS y, symbol, source, COUNT(*) FROM read_parquet('{_glob(CURATED_FNO_UNDERLYING)}')
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """).fetchall()
    print("\nspot rows by source")
    for y, sym, src, n in spot:
        print(f"{y:>4} {sym:10} {src:12} {n:>5}")
