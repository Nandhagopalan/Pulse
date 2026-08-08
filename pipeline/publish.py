"""
Publish the computed snapshot into Supabase.

Only derived state crosses this boundary — one breadth row, a few dozen sector
rows, ~2,500 stock metric rows and the candle cache. The bars themselves stay on
R2. That split is what keeps the hot database inside the free tier while the lake
holds two decades.

Writes go through a direct Postgres connection rather than PostgREST: this is a
few thousand upserts in one transaction, which COPY-style batching does in
seconds and the REST API does in minutes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import psycopg

from .config import config

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()

BATCH = 500


def _executemany(cur, sql: str, rows: Sequence[Tuple], label: str) -> int:
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        cur.executemany(sql, chunk)
        total += len(chunk)
    print(f"  {label}: {total:,} rows")
    return total


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def publish_snapshot(conn, snap: dict) -> None:
    date = snap["date"]
    now = datetime.now(timezone.utc).isoformat()

    with conn.cursor() as cur:
        # ── Breadth: one row per session ─────────────────────────────────────
        cur.execute(
            """INSERT INTO breadth_daily (date, data) VALUES (%s, %s)
               ON CONFLICT (date) DO UPDATE SET data = EXCLUDED.data""",
            (date, json.dumps(snap["breadth"])),
        )

        # ── Sectors and stock metrics: replace the session wholesale ─────────
        # Delete-then-insert rather than upsert: a symbol that dropped out of the
        # universe today must not linger with yesterday's numbers attached to
        # today's date.
        cur.execute("DELETE FROM sector_scores WHERE date = %s", (date,))
        _executemany(
            cur, "INSERT INTO sector_scores (date, sector, data) VALUES (%s, %s, %s)",
            [(date, s["name"], json.dumps(s)) for s in snap["sectors"]], "sectors",
        )

        cur.execute("DELETE FROM stock_metrics WHERE date = %s", (date,))
        _executemany(
            cur, "INSERT INTO stock_metrics (date, symbol, data) VALUES (%s, %s, %s)",
            [(date, s["sym"], json.dumps(s)) for s in snap["stocks"]], "stock metrics",
        )

        # ── Instruments / sector map ─────────────────────────────────────────
        _executemany(
            cur,
            """INSERT INTO instruments (symbol, sector, industry, active) VALUES (%s, %s, %s, 1)
               ON CONFLICT (symbol) DO UPDATE SET sector = EXCLUDED.sector,
                 industry = EXCLUDED.industry, active = 1""",
            [(s["sym"], s["sector"], s["sector"]) for s in snap["stocks"]], "instruments",
        )

        # ── Index history ────────────────────────────────────────────────────
        idx_rows: List[Tuple] = []
        for name, series in snap["indices"].items():
            for i, d in enumerate(series["dates"]):
                idx_rows.append((name, d, series["open"][i], series["high"][i],
                                 series["low"][i], series["close"][i]))
        _executemany(
            cur,
            """INSERT INTO index_bars (index_name, date, open, high, low, close)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (index_name, date) DO UPDATE SET
                 open = EXCLUDED.open, high = EXCLUDED.high,
                 low = EXCLUDED.low, close = EXCLUDED.close""",
            idx_rows, "index bars",
        )

        # ── Candle cache ─────────────────────────────────────────────────────
        _executemany(
            cur,
            """INSERT INTO stock_candles (symbol, date, data, updated_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (symbol) DO UPDATE SET
                 date = EXCLUDED.date, data = EXCLUDED.data, updated_at = EXCLUDED.updated_at""",
            [(sym, date, json.dumps(c), now) for sym, c in snap["candles"].items()], "candles",
        )

        for key, value in (("last_analytics_date", date), ("last_ingested_session", date)):
            cur.execute(
                """INSERT INTO meta (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                (key, value),
            )
        cur.execute(
            "INSERT INTO ingest_log (ts, job, date, status, detail) VALUES (%s, %s, %s, %s, %s)",
            (now, "publish", date, "ok",
             f"{len(snap['stocks'])} stocks, {len(snap['sectors'])} sectors"),
        )
    conn.commit()


def publish_actions(conn) -> None:
    """
    Mirror the applied corporate actions so the UI can explain a price jump.

    Read back from the R2 dataset rather than passed in, so this stays correct
    whether it runs after a rebuild or on its own.
    """
    from . import corporate_actions as ca
    from . import r2
    from .analytics import _uris

    con = r2.duck()
    try:
        _, _, actions_uri, _ = _uris()
        rows = con.execute(
            f"""SELECT symbol, CAST(ex_date AS VARCHAR), kind, factor, subject
                FROM read_parquet('{actions_uri}') WHERE applied
                AND factor BETWEEN {ca.MIN_FACTOR} AND {ca.MAX_FACTOR}"""
        ).fetchall()
    finally:
        con.close()

    with conn.cursor() as cur:
        _executemany(
            cur,
            """INSERT INTO corporate_actions (symbol, ex_date, kind, factor, detail)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (symbol, ex_date, kind) DO UPDATE SET
                 factor = EXCLUDED.factor, detail = EXCLUDED.detail""",
            rows, "corporate actions",
        )
    conn.commit()


def publish_membership(conn) -> None:
    """Index constituent lists — drives the index filters in the screener."""
    from . import r2
    from .reference import CONSTITUENTS_KEY
    from .config import s3_uri

    con = r2.duck()
    try:
        rows = con.execute(
            f"SELECT DISTINCT index_name, symbol FROM read_parquet('{s3_uri(CONSTITUENTS_KEY)}')"
        ).fetchall()
    finally:
        con.close()

    with conn.cursor() as cur:
        # Replace wholesale: constituents leave indices as well as join them.
        cur.execute("DELETE FROM index_membership")
        _executemany(
            cur, "INSERT INTO index_membership (index_name, symbol) VALUES (%s, %s)",
            rows, "index membership",
        )
    conn.commit()


def run(snap: dict = None) -> None:
    from . import analytics

    url = config.require_supabase()
    if snap is None:
        print("[publish] computing snapshot …")
        snap = analytics.compute()

    print(f"[publish] {snap['date']} → Supabase")
    with psycopg.connect(url) as conn:
        ensure_schema(conn)
        publish_snapshot(conn, snap)
        publish_actions(conn)
        publish_membership(conn)
    print("[publish] done")
