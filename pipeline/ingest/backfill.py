"""
Historical backfill: NSE archives → R2.

Two layers, written in one pass:

  raw/nse/bhavcopy/YYYY/YYYY-MM-DD.zip     vendor bytes, untouched
  curated/daily/year=YYYY/data.parquet     normalized bars, one file per year

Resumability comes from the raw layer: a session already in R2 is read back from
R2 instead of NSE, so re-running costs no archive traffic and the curated Parquet
can be rebuilt at any time without a single request to the exchange. That matters
because NSE rate-limits hard and has retired archive paths before.

One Parquet per year (not per session) keeps the scan cheap: ~19 objects instead
of ~4,700, which on a remote object store is the difference between one range
request per year and thousands of round-trips.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import CURATED_DAILY, CURATED_INDEX, RAW_BHAV_PREFIX, RAW_INDEX_PREFIX, config, s3_uri
from ..sources import nse, r2

# Optional on-disk mirror of the object store, keyed identically to R2. Set via
# `--local DIR`. It exists so the one expensive pass over NSE's archives can be
# done once and reused: verified locally first, uploaded to R2 afterwards with
# `sync`, without asking the exchange for the same 4,700 files twice.
LOCAL_ROOT: Optional[Path] = None
USE_R2: bool = True


def use_local(path: Optional[str], with_r2: bool = True) -> None:
    global LOCAL_ROOT, USE_R2
    LOCAL_ROOT = Path(path).expanduser().resolve() if path else None
    USE_R2 = with_r2


def _local_path(key: str) -> Optional[Path]:
    return (LOCAL_ROOT / key) if LOCAL_ROOT else None


def _store_get(key: str) -> Optional[bytes]:
    p = _local_path(key)
    if p is not None and p.exists():
        return p.read_bytes()
    if USE_R2:
        return r2.get_object(key)
    return None


def _store_put(key: str, blob: bytes, content_type: str) -> None:
    p = _local_path(key)
    if p is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    if USE_R2:
        r2.put_object(key, blob, content_type=content_type)


def _store_exists(key: str) -> bool:
    p = _local_path(key)
    if p is not None and p.exists():
        return True
    return r2.object_exists(key) if USE_R2 else False

DAILY_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("date", pa.date32()),
    ("series", pa.string()),
    ("isin", pa.string()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("prev_close", pa.float64()),
    ("volume", pa.int64()),
    ("traded_value", pa.float64()),
    ("trades", pa.int64()),
])

# Earliest session for which ind_close_all_DDMMYYYY.csv exists (verified: 2011
# and earlier return 404 for every date, 2013 onward is complete).
INDEX_CLOSE_FROM = date(2013, 1, 1)

INDEX_SCHEMA = pa.schema([
    ("index_name", pa.string()),
    ("date", pa.date32()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
])


def raw_bhav_key(d: date) -> str:
    return f"{RAW_BHAV_PREFIX}/{d.year}/{d.isoformat()}.zip"


def raw_index_key(d: date) -> str:
    return f"{RAW_INDEX_PREFIX}/{d.year}/{d.isoformat()}.csv"


def daily_key(year: int) -> str:
    return f"{CURATED_DAILY}/year={year}/data.parquet"


def index_key(year: int) -> str:
    return f"{CURATED_INDEX}/year={year}/data.parquet"


def _year_sessions(year: int, start: date, end: date) -> Iterator[date]:
    d = max(date(year, 1, 1), start)
    stop = min(date(year, 12, 31), end)
    while d <= stop:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _fetch_cached(raw_key: str, url: str) -> Tuple[Optional[bytes], bool]:
    """
    Return (bytes, came_from_nse). None means the session does not exist upstream
    — a holiday, or a file NSE never published.
    """
    cached = _store_get(raw_key)
    if cached is not None:
        return cached, False
    blob = nse.fetch(url)
    return blob, blob is not None


def _write_parquet(key: str, rows: List[dict], schema: pa.Schema) -> int:
    if not rows:
        return 0
    table = pa.Table.from_pylist(rows, schema=schema).sort_by(
        [("date", "ascending"), (schema.field(0).name, "ascending")]
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", compression_level=6)
    _store_put(key, buf.getvalue(), "application/vnd.apache.parquet")
    return table.num_rows


def backfill_year(year: int, start: date, end: date, force: bool = False) -> Tuple[int, int]:
    """Ingest one calendar year. Returns (bar rows, index rows) written."""
    is_open_year = end.year == year  # current year keeps growing — always rewrite
    if not force and not is_open_year and _store_exists(daily_key(year)):
        print(f"[backfill] {year}: curated parquet present — skipping")
        return 0, 0

    bars: List[dict] = []
    idx_rows: List[dict] = []
    failed: List[date] = []
    sessions = holidays = fetched = 0

    for d in _year_sessions(year, start, end):
        # A transient failure on one of ~4,700 files must not discard the whole
        # year's work. Failures are collected and reported; re-running with
        # --force retries exactly those, since everything else is cached.
        try:
            blob, from_nse = _fetch_cached(raw_bhav_key(d), nse.bhav_url(d))
            if blob is None:
                holidays += 1
                continue
            if from_nse:
                _store_put(raw_bhav_key(d), blob, "application/zip")
                fetched += 1
            bars.extend(nse.parse_bhavcopy(blob, d))
            sessions += 1
        except Exception as err:  # noqa: BLE001
            print(f"[backfill] {d}: bhavcopy FAILED: {err}", flush=True)
            failed.append(d)
            continue

        # NSE only publishes the all-indices close file from 2013 (2011 and
        # earlier 404 for every date). Asking anyway doubles the request count
        # for six years of history to learn something we already know, so the
        # index series simply starts later than the stock series.
        if d < INDEX_CLOSE_FROM:
            continue
        try:
            iblob, i_from_nse = _fetch_cached(raw_index_key(d), nse.index_close_url(d))
            if iblob is not None:
                if i_from_nse:
                    _store_put(raw_index_key(d), iblob, "text/csv")
                idx_rows.extend(nse.parse_index_close(iblob, d))
        except Exception as err:  # noqa: BLE001 — index closes are secondary to bars
            print(f"[backfill] {d}: index close failed: {err}", flush=True)

        if sessions % 25 == 0:
            print(f"[backfill] {year}: {sessions} sessions, {len(bars):,} bars …", flush=True)

    n_bars = _write_parquet(daily_key(year), bars, DAILY_SCHEMA)
    n_idx = _write_parquet(index_key(year), idx_rows, INDEX_SCHEMA)
    print(
        f"[backfill] {year}: {sessions} sessions ({fetched} from NSE, "
        f"{sessions - fetched} cached), {holidays} non-sessions, "
        f"{n_bars:,} bars, {n_idx:,} index rows",
        flush=True,
    )
    if failed:
        print(f"[backfill] {year}: {len(failed)} session(s) FAILED and are missing from "
              f"the lake: {', '.join(str(d) for d in failed)}", flush=True)
    return n_bars, n_idx


def run(start: Optional[date] = None, end: Optional[date] = None, force: bool = False) -> None:
    if USE_R2:
        r2.preflight()
    start = start or date.fromisoformat(config.history_start)
    end = end or date.today()
    dest = f"r2://{config.r2_bucket}" if USE_R2 else str(LOCAL_ROOT)
    print(f"[backfill] {start} → {end} into {dest}")

    total_bars = total_idx = 0
    for year in range(start.year, end.year + 1):
        b, i = backfill_year(year, start, end, force=force)
        total_bars += b
        total_idx += i
    print(f"[backfill] done: {total_bars:,} bars, {total_idx:,} index rows")


def sync_to_r2(prefixes: Optional[List[str]] = None) -> int:
    """
    Upload a local mirror to R2, skipping objects already present.

    The point of the local mode is that NSE gets asked for each file exactly
    once; this replays that captured data into the bucket afterwards.
    """
    if LOCAL_ROOT is None:
        raise RuntimeError("sync requires --local DIR")
    r2.preflight()
    types = {".zip": "application/zip", ".csv": "text/csv",
             ".json": "application/json", ".parquet": "application/vnd.apache.parquet"}
    sent = skipped = 0
    for path in sorted(LOCAL_ROOT.rglob("*")):
        if not path.is_file():
            continue
        key = str(path.relative_to(LOCAL_ROOT))
        if prefixes and not any(key.startswith(p) for p in prefixes):
            continue
        if r2.object_exists(key):
            skipped += 1
            continue
        r2.put_object(key, path.read_bytes(), content_type=types.get(path.suffix, "application/octet-stream"))
        sent += 1
        if sent % 200 == 0:
            print(f"[sync] {sent:,} uploaded …", flush=True)
    print(f"[sync] {sent:,} uploaded, {skipped:,} already present")
    return sent


def daily_glob() -> str:
    """Glob for the curated daily dataset, local mirror or R2 depending on mode."""
    if LOCAL_ROOT is not None and not USE_R2:
        return str(LOCAL_ROOT / CURATED_DAILY / "*" / "data.parquet")
    return s3_uri(f"{CURATED_DAILY}/*/data.parquet")


def index_glob() -> str:
    if LOCAL_ROOT is not None and not USE_R2:
        return str(LOCAL_ROOT / CURATED_INDEX / "*" / "data.parquet")
    return s3_uri(f"{CURATED_INDEX}/*/data.parquet")


def lake_summary(con=None) -> None:
    """Print what the curated lake actually contains — the post-backfill sanity check."""
    con = con or (r2.duck() if USE_R2 else __import__("duckdb").connect())
    row = con.execute(
        f"""
        SELECT COUNT(*) AS bars, COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT date) AS sessions, MIN(date) AS first, MAX(date) AS last
        FROM read_parquet('{daily_glob()}')
        """
    ).fetchone()
    print(f"bars={row[0]:,}  symbols={row[1]:,}  sessions={row[2]:,}  range={row[3]} → {row[4]}")
