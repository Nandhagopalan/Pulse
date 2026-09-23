"""
Symbol renames, and the history they would otherwise strand.

NSE lets a company change its trading symbol without changing what it is. The
bhavcopy records whatever the symbol was on the day, so the tape hands over
mid-series: HEG prints until 2026-09-21 and HEGAM from 2026-09-22, and nothing
in the bars says they are the same company. Keyed by symbol alone, nineteen
years of HEG become a dead series and HEG Advanced Materials becomes a stock
that listed yesterday.

That is not a cosmetic problem. An all-time high is computed over what the
symbol holds, so a renamed company's high is whatever it managed since the
rename: 139 symbols still trading are currently below an all-time high the lake
has, under their old name. It is exactly the shape that had AHCL reading 88%
below a high it was sitting on, and the same shape NSE's own charts do not
have, because NSE applies this mapping when it serves history.

Source is NSE's symbol change master, which is the authoritative list and lives
on the archive host rather than the API host, so it is reachable on the same
terms as the bhavcopy.

The map is applied at *compute* time, never by rewriting bars. A stored bar is
what the tape printed, and the symbol it printed under is part of that.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import CURATED_INSTRUMENTS, s3_uri
from ..sources import nse, r2

CHANGES_KEY = f"{CURATED_INSTRUMENTS}/symbol_changes.parquet"
CHANGES_URI = s3_uri(CHANGES_KEY)
SOURCE_URL = f"{nse.ARCHIVES}/content/equities/symbolchange.csv"

SCHEMA = pa.schema([
    ("company", pa.string()),
    ("old_symbol", pa.string()),
    ("new_symbol", pa.string()),
    ("effective", pa.date32()),
])


def parse(blob: bytes) -> List[dict]:
    """
    Rows of NSE's symbol change master: company, old symbol, new symbol, date.

    The file has no header and carries a few self-referential rows — debt
    instruments filed as renaming to themselves — which are dropped here rather
    than left for the closure to trip over.
    """
    rows: List[dict] = []
    seen = set()
    for rec in csv.reader(io.StringIO(blob.decode("utf8", "replace"))):
        if len(rec) < 4:
            continue
        company, old, new, when = (c.strip() for c in rec[:4])
        eff: Optional[date] = None
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
            try:
                eff = datetime.strptime(when, fmt).date()
                break
            except ValueError:
                continue
        if not old or not new or old == new or eff is None:
            continue
        if (old, new, eff) in seen:
            continue
        seen.add((old, new, eff))
        rows.append({"company": company, "old_symbol": old,
                     "new_symbol": new, "effective": eff})
    return rows


def refresh() -> int:
    """Fetch the master and overwrite the stored copy. Current state, not history."""
    blob = nse.fetch(SOURCE_URL)
    if not blob:
        raise RuntimeError(f"symbol change master unreachable: {SOURCE_URL}")
    rows = parse(blob)
    if not rows:
        raise RuntimeError("symbol change master parsed to nothing")
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), buf, compression="zstd")
    r2.put_object(CHANGES_KEY, buf.getvalue(),
                  content_type="application/vnd.apache.parquet")
    print(f"[symbols] {len(rows)} renames → {CHANGES_KEY}")
    return len(rows)


def resolve_chain(pairs: List[Tuple[str, str]]) -> Dict[str, str]:
    """
    Every old symbol mapped to the name it ends up under today.

    Renames chain — APPAPER became IPAPPM became ANDPAPER became ANDHRAPAP —
    and 148 of them do. Walking with a visited set rather than recursing,
    because the feed contains cycles: a symbol that renames to itself, and
    pairs that renamed back and forth.
    """
    nxt = dict(pairs)
    out: Dict[str, str] = {}
    for old in nxt:
        seen = {old}
        cur = old
        while cur in nxt and nxt[cur] not in seen:
            cur = nxt[cur]
            seen.add(cur)
        if cur != old:
            out[old] = cur
    return out


def predecessors(rows: List[dict]) -> Dict[str, Tuple[str, date]]:
    """
    Each symbol mapped to the name it was trading under just before it.

    The inverse of the rename, which is the direction a corporate action has to
    be read in: NSE files every action under the symbol the company carries
    *today*, so answering "which bars did this re-base?" means walking back to
    the ex-date. Keyed by the new name; where a symbol has been arrived at more
    than once the earliest handover is kept, so the walk always moves backwards.
    """
    out: Dict[str, Tuple[str, date]] = {}
    for r in sorted(rows, key=lambda x: x["effective"]):
        new, old, eff = r["new_symbol"], r["old_symbol"], r["effective"]
        if new not in out:
            out[new] = (old, eff)
    return out


def symbol_at(symbol: str, when: date, prev: Dict[str, Tuple[str, date]]) -> str:
    """
    The symbol this company traded under on `when`.

    Walks back through every rename that happened after that date. The visited
    set is not paranoia: the master contains rows that rename a symbol to
    itself and pairs that were renamed back and forth, and either would spin
    here forever.
    """
    cur = symbol
    seen = {cur}
    while cur in prev:
        old, eff = prev[cur]
        if eff <= when or old in seen:
            break
        cur = old
        seen.add(cur)
    return cur
