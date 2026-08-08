"""
Reference data: index membership and the sector mapping that drives sector scores.

These NSE files describe *current* state, not history — there is no archive of
"who was in NIFTY 500 in 2011". So this is a snapshot, refreshed on every run and
overwritten in place, unlike the append-only bar datasets.
"""
from __future__ import annotations

import io
from typing import Dict, List

import pyarrow as pa
import pyarrow.parquet as pq

from . import nse, r2
from .config import CURATED_INSTRUMENTS, s3_uri

INDEX_LISTS: Dict[str, str] = {
    "NIFTY 50": "/content/indices/ind_nifty50list.csv",
    "NIFTY 500": "/content/indices/ind_nifty500list.csv",
    "NIFTY MIDCAP 100": "/content/indices/ind_niftymidcap100list.csv",
    "NIFTY SMLCAP 100": "/content/indices/ind_niftysmallcap100list.csv",
    "NIFTY BANK": "/content/indices/ind_niftybanklist.csv",
    "NIFTY IT": "/content/indices/ind_niftyitlist.csv",
}

CONSTITUENTS_KEY = f"{CURATED_INSTRUMENTS}/constituents.parquet"
CONSTITUENTS_URI = s3_uri(CONSTITUENTS_KEY)

SCHEMA = pa.schema([
    ("index_name", pa.string()),
    ("symbol", pa.string()),
    ("name", pa.string()),
    ("industry", pa.string()),
    ("isin", pa.string()),
])


def refresh() -> int:
    rows: List[dict] = []
    for index_name, path in INDEX_LISTS.items():
        blob = nse.fetch(nse.ARCHIVES + path)
        if blob is None:
            print(f"[reference] {index_name}: list unavailable — skipped")
            continue
        for r in nse._rows(blob.decode("utf8", errors="replace")):
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            rows.append({
                "index_name": index_name,
                "symbol": sym,
                "name": (r.get("company name") or "").strip() or None,
                "industry": (r.get("industry") or "").strip() or None,
                "isin": (r.get("isin code") or "").strip() or None,
            })
        print(f"[reference] {index_name}: {sum(1 for x in rows if x['index_name'] == index_name)} symbols")

    if not rows:
        raise RuntimeError("no index constituent lists could be fetched")

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), buf, compression="zstd")
    r2.put_object(CONSTITUENTS_KEY, buf.getvalue(), content_type="application/vnd.apache.parquet")
    return len(rows)
