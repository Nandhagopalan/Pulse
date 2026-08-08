"""
Corporate action extraction and price adjustment.

Splits and bonuses re-base a stock's price overnight. Left uncorrected, RELIANCE
looks like it fell 50% on 2017-09-07 and has been "90% below its all-time high"
ever since. Every ATH, EMA and 52-week figure downstream depends on getting this
right, so the events get their own audited dataset rather than being folded
silently into the bars.

Source of truth is NSE's corporate actions feed, which covers the full history
and states the action explicitly ("Bonus 1:1", "Face Value Split ... From Rs 10/-
To Rs 2/-"). We deliberately do NOT infer actions from the bhavcopy's PREVCLOSE
column: NSE does not restate it on an ex-date (verified on the RELIANCE bonus,
where PREVCLOSE reads the unadjusted 1645.40), so that signal does not exist.

Every parsed factor is then verified against what the tape actually did — the
close-to-close gap at the ex-date must agree with the stated ratio. That catches
mis-parsed labels, actions announced but never executed, and ex-dates recorded a
session off. Only verified events are applied.

Convention downstream: `adjusted = raw / k` for bars *before* the ex-date, and
`volume * k`, matching server/src/analytics/engine.ts.
"""
from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from . import nse, r2
from .config import CURATED_ACTIONS, CURATED_DAILY, config, s3_uri

if TYPE_CHECKING:
    import duckdb

ACTIONS_KEY = f"{CURATED_ACTIONS}/actions.parquet"
RAW_CA_PREFIX = "raw/nse/corp_actions"

# Real actions live inside this band; anything outside is a parse error or a
# garbled source row, not a 50:1 split.
MIN_FACTOR, MAX_FACTOR = 0.05, 20.0
# How far the stated ratio may sit from the observed price gap before we refuse
# to apply it. Generous, because the stock also moves on its own that day.
VERIFY_TOLERANCE = 0.25
# Ex-dates in the feed are occasionally a session off; search this far either way.
EX_DATE_SLACK = 3

SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("ex_date", pa.date32()),
    ("factor", pa.float64()),
    ("kind", pa.string()),          # bonus | split | bonus+split
    ("status", pa.string()),        # verified | unverified | no_bars
    ("implied", pa.float64()),      # ratio the tape actually showed
    ("subject", pa.string()),       # raw label, kept for audit
    ("source_ex_date", pa.date32()),
    ("applied", pa.bool_()),
])

# ── Label parsing ────────────────────────────────────────────────────────────
# "Bonus 1:1", "Bonus 1 : 1", "Bonus 10:1"
_BONUS_RE = re.compile(r"bonus\s+(\d+)\s*:\s*(\d+)")
# "From Rs 10/- Per Share To Rs 2/- Per Share", "Rs 10 To Re 1", "From Rs.5/- To Rs.1/-".
# `from` is optional: the feed also writes bare "Face Value Split Rs 10 To Rs 1".
_SPLIT_RE = re.compile(
    r"(?:from\s*)?(?:rs|re)\.?\s*(\d+(?:\.\d+)?)\s*/?-?.*?to\s*(?:rs|re)\.?\s*(\d+(?:\.\d+)?)",
)
# Smallest ratio we treat as a real equity action. A 1:20 bonus is 1.05; the feed
# also carries oddities like "Bonus 1 : 1250" (k=1.0008) that are not equity
# bonuses at all and would otherwise slip through as no-op factors.
MIN_MEANINGFUL_FACTOR = 1.02
# A bonus issue of *debentures* leaves the share count untouched — no adjustment.
_NOT_EQUITY_BONUS = re.compile(r"bonus\s+debenture")


def parse_factor(subject: str) -> Tuple[Optional[float], str]:
    """
    Derive the price adjustment factor from an NSE corporate-action label.

    A 1:1 bonus doubles the share count, so the price halves and k = 2.
    A face-value split from Rs 10 to Rs 2 is a 5:1 split, so k = 5.
    Both can appear in one label ("Bonus 1:1 / Face Value Split From Rs 10 To
    Rs 2"), in which case they compound to k = 10.
    """
    s = " ".join(subject.lower().split())
    factor = 1.0
    kinds: List[str] = []

    if not _NOT_EQUITY_BONUS.search(s):
        m = _BONUS_RE.search(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0 and a > 0:
                f = (a + b) / b
                if MIN_MEANINGFUL_FACTOR <= f <= MAX_FACTOR:
                    factor *= f
                    kinds.append("bonus")

    if "split" in s or "sub-div" in s or "sub div" in s or "subdivision" in s:
        m = _SPLIT_RE.search(s)
        if m:
            frm, to = float(m.group(1)), float(m.group(2))
            if to > 0 and frm > to:
                f = frm / to
                if 1.0 < f <= MAX_FACTOR:
                    factor *= f
                    kinds.append("split")

    if not kinds or factor <= 1.0 or factor > MAX_FACTOR:
        return None, "other"
    return factor, "+".join(kinds)


# ── Fetch ────────────────────────────────────────────────────────────────────
def _raw_key(year: int) -> str:
    return f"{RAW_CA_PREFIX}/{year}.json"


def fetch_year(year: int, refresh: bool = False) -> list:
    """
    One calendar year of corporate actions, cached in R2.

    Cached because this endpoint lives on www.nseindia.com, which blocks
    datacenter IPs far more aggressively than the archive host — so a CI run can
    work from the cache even when it cannot reach the API itself.
    """
    key = _raw_key(year)
    if not refresh:
        cached = r2.get_object(key)
        if cached is not None:
            return json.loads(cached)
    data = nse.fetch_www_json(nse.corporate_actions_url(date(year, 1, 1), date(year, 12, 31)))
    r2.put_object(key, json.dumps(data).encode(), content_type="application/json")
    return data


def _parse_ex_date(s: str) -> Optional[date]:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


# ── Verification against the tape ────────────────────────────────────────────
VERIFY_SQL = """
WITH bars AS (
    SELECT symbol, date, close,
           LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close,
           LAG(date)  OVER (PARTITION BY symbol ORDER BY date) AS prev_date
    FROM read_parquet('{daily_glob}')
    WHERE close > 0
)
SELECT symbol, date, prev_close / close AS implied
FROM bars
WHERE prev_close IS NOT NULL
  AND date - prev_date <= 15   -- a hole in history is not a corporate action
  AND prev_close / close BETWEEN {min_f} AND {max_f}
"""


def build(con: Optional["duckdb.DuckDBPyConnection"] = None,
          start_year: Optional[int] = None,
          end_year: Optional[int] = None,
          refresh: bool = False,
          refresh_years: Optional[set] = None,
          write: bool = True) -> pa.Table:
    """Fetch, parse, verify and persist the corporate action dataset."""
    own = con is None
    con = con or r2.duck()
    try:
        start_year = start_year or date.fromisoformat(config.history_start).year
        end_year = end_year or date.today().year

        # Every close-to-close gap in the lake, keyed for lookup.
        gaps = con.execute(
            VERIFY_SQL.format(daily_glob=s3_uri(f"{CURATED_DAILY}/*/data.parquet"),
                              min_f=MIN_FACTOR, max_f=MAX_FACTOR)
        ).fetchall()
        gap_by_symbol: dict = {}
        for sym, d, implied in gaps:
            gap_by_symbol.setdefault(sym, []).append((d, implied))
        for lst in gap_by_symbol.values():
            lst.sort()

        rows: List[dict] = []
        for year in range(start_year, end_year + 1):
            # Closed years never change, so they are served from the R2 cache;
            # the current year must be re-fetched or tonight's ex-dates are missed.
            want_fresh = refresh or (refresh_years is not None and year in refresh_years)
            try:
                data = fetch_year(year, refresh=want_fresh)
            except Exception as err:  # noqa: BLE001 — one bad year must not sink the build
                print(f"[actions] {year}: fetch failed ({err}) — skipped")
                continue

            year_events = 0
            for r in data:
                subject = (r.get("subject") or "").strip()
                factor, kind = parse_factor(subject)
                if factor is None:
                    continue
                sym = (r.get("symbol") or "").strip()
                ex = _parse_ex_date(r.get("exDate") or "")
                if not sym or ex is None:
                    continue

                # Snap to the session whose price gap best matches the stated
                # ratio: feed ex-dates are occasionally a session off, and the
                # tape is the thing our bars actually contain.
                best = None
                for d, implied in gap_by_symbol.get(sym, []):
                    if abs((d - ex).days) > EX_DATE_SLACK:
                        continue
                    err_rel = abs(implied / factor - 1.0)
                    if best is None or err_rel < best[2]:
                        best = (d, implied, err_rel)

                if best is None:
                    status, ex_used, implied_val = "no_bars", ex, None
                elif best[2] <= VERIFY_TOLERANCE:
                    status, ex_used, implied_val = "verified", best[0], best[1]
                else:
                    status, ex_used, implied_val = "unverified", ex, best[1]

                rows.append({
                    "symbol": sym,
                    "ex_date": ex_used,
                    "factor": factor,
                    "kind": kind,
                    "status": status,
                    "implied": implied_val,
                    "subject": subject,
                    "source_ex_date": ex,
                    "applied": status == "verified",
                })
                year_events += 1
            print(f"[actions] {year}: {len(data):,} filings → {year_events} split/bonus events")

        # Same symbol + same ex-date can appear twice when a filing is revised.
        seen = set()
        deduped = []
        for r in sorted(rows, key=lambda x: (x["symbol"], x["ex_date"], not x["applied"])):
            key = (r["symbol"], r["ex_date"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)

        table = pa.Table.from_pylist(deduped, schema=SCHEMA)
        if write:
            buf = io.BytesIO()
            pq.write_table(table, buf, compression="zstd")
            r2.put_object(ACTIONS_KEY, buf.getvalue(), content_type="application/vnd.apache.parquet")
        return table
    finally:
        if own:
            con.close()


def summary(table: pa.Table) -> str:
    status = Counter(table.column("status").to_pylist())
    kinds = Counter(table.column("kind").to_pylist())
    return (f"{table.num_rows:,} events — "
            + ", ".join(f"{k}: {v:,}" for k, v in sorted(status.items()))
            + " | " + ", ".join(f"{k}: {v:,}" for k, v in sorted(kinds.items())))


# ── Adjustment ───────────────────────────────────────────────────────────────
def adjusted_bars_cte(daily_glob: str, actions_uri: str, min_date: Optional[str] = None) -> str:
    """
    SQL CTEs yielding `bars_adj`: every bar with its cumulative factor `k` and
    split-adjusted OHLCV.

    k(d) = product of all factors with ex_date > d. Computed as
    total_product / product_up_to_d, which turns what would be a range join over
    8M bars into a single backward ASOF join.
    """
    date_filter = f"AND date >= DATE '{min_date}'" if min_date else ""
    return f"""
WITH bars AS (
    SELECT symbol, date, open, high, low, close, volume, traded_value
    FROM read_parquet('{daily_glob}')
    WHERE close > 0 {date_filter}
),
acts AS (
    SELECT symbol, ex_date, factor
    FROM read_parquet('{actions_uri}')
    WHERE applied AND factor BETWEEN {MIN_FACTOR} AND {MAX_FACTOR}
),
acts_cum AS (
    SELECT symbol, ex_date,
           exp(SUM(ln(factor)) OVER (PARTITION BY symbol ORDER BY ex_date
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS q
    FROM acts
),
acts_total AS (
    SELECT symbol, exp(SUM(ln(factor))) AS p FROM acts GROUP BY symbol
),
bars_k AS (
    SELECT b.*, COALESCE(t.p, 1.0) / COALESCE(c.q, 1.0) AS k
    FROM bars b
    LEFT JOIN acts_total t ON t.symbol = b.symbol
    ASOF LEFT JOIN acts_cum c
      ON c.symbol = b.symbol AND b.date >= c.ex_date
),
bars_adj AS (
    SELECT symbol, date,
           open / k AS open, high / k AS high, low / k AS low, close / k AS close,
           volume * k AS volume, traded_value, k
    FROM bars_k
)
"""
