"""
Industry classification for the whole lake, keyed by ISIN.

The sector overlay in the strategy engine reads `constituents.parquet`, which is
today's NIFTY 500 — 500 labels for 3,967 equities. Everything else is unlabeled,
and the overlay excludes what it cannot classify, so the rule silently reduces to
"only hold companies that are in the NIFTY 500 today". Applied across 2008-2024
that is a look-ahead; applied now it is an unintended large-cap screen.

This dataset replaces that with labels drawn from BSE's scrip master, which keeps
delisted and suspended companies. Coverage is ~81% of the lake's equities and the
same ~81% of the delisted ones, so what remains unlabeled does not correlate with
whether the company survived.

    python -m pipeline industry            # build, reusing what is already cached
    python -m pipeline industry --refresh  # re-fetch every scrip

Two calls list the scrips; the classification then costs one call per scrip, so
a cold build is a few thousand requests. Rows already stored are not re-fetched
unless `--refresh` is passed, which makes a failed run cheap to resume.
"""
from __future__ import annotations

import io
from time import sleep
from typing import Dict, List, Optional, Set

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import CURATED_DAILY, CURATED_INSTRUMENTS, config, s3_uri
from ..sources import bse, nseapi, r2

INDUSTRY_KEY = f"{CURATED_INSTRUMENTS}/industry.parquet"
INDUSTRY_URI = s3_uri(INDUSTRY_KEY)

SCHEMA = pa.schema([
    ("isin", pa.string()),
    ("scrip_code", pa.string()),
    ("bse_symbol", pa.string()),
    ("name", pa.string()),
    ("status", pa.string()),
    ("industry", pa.string()),   # basic industry, e.g. "Auto Components & Equipments"
    ("macro", pa.string()),      # macro sector,    e.g. "Automobile and Auto Components"
    ("group", pa.string()),      # the level above sector, NSE only
    ("source", pa.string()),     # which exchange answered; provenance, not decoration
])


def _lake_symbols(con) -> List[tuple]:
    """
    (symbol, isin, last bar date) for every equity issue in the lake.

    Stage two asks NSE by ticker, and NSE only knows companies that are still
    listed, so the last bar date is what decides whether asking is worthwhile.
    """
    glob = s3_uri(f"{CURATED_DAILY}/*/data.parquet")
    rows = con.execute(
        f"""
        SELECT symbol, any_value(isin) AS isin, max(date) AS last
        FROM read_parquet('{glob}')
        WHERE isin IS NOT NULL AND starts_with(isin, 'INE')
        GROUP BY symbol
        """
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _lake_isins(con) -> Set[str]:
    """
    Every ISIN the lake actually has bars for.

    The scrip master carries ~8,500 ISINs, most of which never traded on NSE.
    Fetching a classification for those would triple the request count for names
    the strategy can never hold, so the work is restricted to what is tradeable
    here. Equity issues only: INF is a fund unit and has no industry.
    """
    glob = s3_uri(f"{CURATED_DAILY}/*/data.parquet")
    rows = con.execute(
        f"SELECT DISTINCT isin FROM read_parquet('{glob}') "
        "WHERE isin IS NOT NULL AND starts_with(isin, 'INE')"
    ).fetchall()
    return {r[0] for r in rows}


def _cached() -> Dict[str, dict]:
    """Rows already stored, so a re-run resumes rather than restarts."""
    blob = r2.get_object(INDUSTRY_KEY)
    if blob is None:
        return {}
    tbl = pq.read_table(io.BytesIO(blob))
    out: Dict[str, dict] = {}
    for row in tbl.to_pylist():
        if row.get("isin"):
            row.setdefault("group", None)
            row.setdefault("source", "bse")
            out[row["isin"]] = row
    return out


def build(refresh: bool = False, write: bool = True, limit: Optional[int] = None) -> pa.Table:
    con = r2.duck()
    try:
        wanted = _lake_isins(con)
    finally:
        con.close()
    print(f"[industry] lake has {len(wanted)} equity ISINs")

    master = bse.scrip_master()
    # One scrip per ISIN. Active beats delisted where a company appears twice:
    # the live listing is the one whose classification BSE keeps current.
    by_isin: Dict[str, dict] = {}
    rank = {s: i for i, s in enumerate(bse.STATUSES)}
    for r in master:
        isin = (r.get("ISIN_NUMBER") or "").strip()
        if not isin or isin not in wanted:
            continue
        prev = by_isin.get(isin)
        if prev is None or rank[r["Status"]] < rank[prev["Status"]]:
            by_isin[isin] = r
    print(f"[industry] {len(by_isin)} of them matched to a BSE scrip "
          f"({len(by_isin) / max(len(wanted), 1):.0%})")

    have = {} if refresh else _cached()
    todo = [i for i in by_isin if i not in have or not have[i].get("macro")]
    if limit:
        todo = todo[:limit]
    print(f"[industry] {len(have)} cached, {len(todo)} to fetch")

    rows: List[dict] = [have[i] for i in by_isin if i in have and i not in set(todo)]
    failed = 0
    for n, isin in enumerate(todo, start=1):
        src = by_isin[isin]
        code = str(src["SCRIP_CD"])
        try:
            info = bse.header(code)
        except Exception as err:  # noqa: BLE001 — one bad scrip must not lose the run
            failed += 1
            if failed <= 5:
                print(f"[industry] {code}: {err}")
            info = {"industry": None, "macro": None}
        rows.append({
            "isin": isin,
            "scrip_code": code,
            "bse_symbol": (src.get("scrip_id") or "").strip() or None,
            "name": (src.get("Scrip_Name") or "").strip() or None,
            "status": src["Status"],
            "industry": info["industry"],
            "macro": info["macro"],
            "group": None,
            "source": "bse",
        })
        sleep(config.nse_delay)
        if n % 250 == 0:
            print(f"[industry] {n}/{len(todo)} fetched", flush=True)

    # ── stage two: NSE, for what BSE could not label ────────────────────────
    # BSE misses NSE-only listings, and returns no classification for ~14% of
    # the scrips it does match. NSE covers both gaps for companies still listed,
    # in the same vocabulary, so the two merge without a mapping.
    labelled = {r["isin"] for r in rows if r.get("macro")}
    con = r2.duck()
    try:
        lake = _lake_symbols(con)
    finally:
        con.close()
    cutoff = max((row[2] for row in lake), default=None)
    recent = [s for s in lake
              if s[1] not in labelled and cutoff is not None
              and (cutoff - s[2]).days <= 60]
    if limit:
        recent = recent[:limit]
    print(f"[industry] {len(recent)} still-listed symbols unlabelled after BSE")

    added = 0
    try:
        for n, (sym, isin, _last) in enumerate(recent, start=1):
            try:
                found = nseapi.classification(sym)
            except Exception:  # noqa: BLE001 — an unresolvable ticker is expected
                found = None
            if found and found.get("macro"):
                rows.append({
                    "isin": isin,
                    "scrip_code": None,
                    "bse_symbol": sym,
                    "name": None,
                    "status": "Active",
                    "industry": found["industry"],
                    "macro": found["macro"],
                    "group": found["group"],
                    "source": "nse",
                })
                added += 1
            sleep(config.nse_delay)
            if n % 250 == 0:
                print(f"[industry] NSE {n}/{len(recent)}, {added} labelled", flush=True)
    finally:
        nseapi.close()
    print(f"[industry] NSE added {added} labels")

    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    if write and rows:
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd")
        r2.put_object(INDUSTRY_KEY, buf.getvalue(),
                      content_type="application/vnd.apache.parquet")
    return table


def summary(table: pa.Table) -> str:
    macro = [m for m in table.column("macro").to_pylist() if m]
    ind = [i for i in table.column("industry").to_pylist() if i]
    src = [s for s, m in zip(table.column("source").to_pylist(),
                             table.column("macro").to_pylist(), strict=True) if m]
    n = table.num_rows
    return (f"{n} rows, {len(macro)} with a sector ({len(macro) / max(n, 1):.0%}) "
            f"[bse {src.count('bse')}, nse {src.count('nse')}], "
            f"{len(set(macro))} distinct sectors, {len(set(ind))} distinct industries")
