"""
Publish the computed snapshot into Supabase.

Only derived state crosses this boundary — one breadth row, a few dozen sector
rows, ~2,500 stock metric rows and the candle cache. The bars themselves stay on
R2. That split is what keeps the hot database inside the free tier while the lake
holds two decades.

Writes go through a direct Postgres connection rather than PostgREST: this is a
few thousand upserts in one transaction, which COPY-style batching does in
seconds and the REST API does in minutes.

The schema itself is not this module's business: supabase/migrations owns it,
applied with `supabase db push`. This job assumes the tables are already there.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import List, Sequence, Tuple

import psycopg

from .config import config

BATCH = 500


def _executemany(cur, sql: str, rows: Sequence[Tuple], label: str) -> int:
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        cur.executemany(sql, chunk)
        total += len(chunk)
    print(f"  {label}: {total:,} rows")
    return total


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


FII_DII_PATH = "/api/fiidiiTradeReact"
FII_DII_REFERER = "/reports/fii-dii"


def publish_flows(conn) -> None:
    """
    FII/DII provisional flows.

    Not part of the lake: NSE publishes only a rolling two-day window with no
    archive, so there is nothing to backfill and no Parquet dataset to derive
    this from — each run captures what is on the page and upserts it. This
    used to run in the Node server; it moved here when that server stopped
    doing ingestion of any kind.

    Best-effort by design. The endpoint lives on www.nseindia.com, which blocks
    datacenter IPs far more aggressively than the archive host, so a failure
    logs and returns rather than failing the nightly chain over a sidebar.
    """
    from . import nse

    try:
        rows = nse.fetch_www_json(FII_DII_PATH, referer=FII_DII_REFERER)
    except Exception as err:  # noqa: BLE001 — a blocked scrape must not fail EOD
        print(f"  fii/dii: unavailable ({err})")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingest_log (ts, job, date, status, detail) VALUES (%s, %s, %s, %s, %s)",
                (datetime.now(timezone.utc).isoformat(), "fii_dii",
                 date.today().isoformat(), "failed", str(err)[:500]),
            )
        conn.commit()
        return

    out: List[Tuple] = []
    for r in rows:
        category = (r.get("category") or "").upper()
        # The feed labels the foreign side FII or FPI depending on the day.
        side = "FII" if "FII" in category or "FPI" in category else "DII"
        try:
            d = datetime.strptime(r["date"].strip(), "%d-%b-%Y").date().isoformat()
        except (KeyError, ValueError):
            d = date.today().isoformat()
        out.append((d, side, _f(r.get("buyValue")), _f(r.get("sellValue")), _f(r.get("netValue"))))

    with conn.cursor() as cur:
        _executemany(
            cur,
            """INSERT INTO fii_dii (date, category, buy, sell, net)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (date, category) DO UPDATE SET
                 buy = EXCLUDED.buy, sell = EXCLUDED.sell, net = EXCLUDED.net""",
            out, "fii/dii flows",
        )
    conn.commit()


def _f(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def run(snap: dict = None) -> None:
    from . import analytics

    url = config.require_supabase()
    if snap is None:
        print("[publish] computing snapshot …")
        snap = analytics.compute()

    print(f"[publish] {snap['date']} → Supabase")
    with psycopg.connect(url) as conn:
        publish_snapshot(conn, snap)
        publish_flows(conn)
    print("[publish] done")
