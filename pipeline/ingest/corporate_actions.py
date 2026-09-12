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

The unit of work here is the **event** — one symbol on one ex-date — and not the
filing. A company that declares a bonus and a face-value split effective the same
day files them as two rows, and their factors multiply: AHCL's 2026-04-24 is
"Bonus 1:1" alongside a 10-to-2 split, a 10x re-basing that neither row states.
Filings are therefore grouped and compounded before anything else happens to
them. Treating the second row as a duplicate of the first — which is what the
feed looks like at a glance — leaves both halves understating the gap, so both
fail verification and nothing is applied at all.

Every event's factor is then verified against what the tape actually did — the
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
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import CURATED_ACTIONS, CURATED_DAILY, config, s3_uri
from ..sources import nse, r2

if TYPE_CHECKING:
    import duckdb

ACTIONS_KEY = f"{CURATED_ACTIONS}/actions.parquet"
RAW_CA_PREFIX = "raw/nse/corp_actions"

# Three bands, because the old single pair was quietly doing three different
# jobs and the smallest of the three was winning every time.
#
# 1. One *component* — the bonus half or the split half of a label. Bounded by
#    what an issuer can actually declare: face values run Rs 100 down to Re 1,
#    and DPSCLTD really did announce a 22:1 bonus (k=23).
MIN_FACTOR, MAX_FACTOR = 0.05, 100.0
# 2. One *event* — every filing sharing a symbol and an ex-date, compounded. A
#    bonus and a face-value split declared effective the same day are filed as
#    two separate rows and multiply: SARVESHWAR's 2023-09-15 is a 10-to-1 split
#    alongside a 2:1 bonus, k=30. Bounded by the tape scan below, since an event
#    larger than the widest gap we will look at can never be verified anyway.
# 3. The *tape scan* verification measures against. Widest of the three on
#    purpose: a gap outside this band is never examined, so a band that merely
#    covered what we expect to find would filter out the very evidence it exists
#    to weigh — a compounded 30x event, or the ~100x an ETF unit split prints.
MIN_GAP_FACTOR, MAX_GAP_FACTOR = 0.004, 250.0
MAX_EVENT_FACTOR = MAX_GAP_FACTOR
# How far the stated ratio may sit from the observed price gap before we refuse
# to apply it. Generous, because the stock also moves on its own that day.
VERIFY_TOLERANCE = 0.25
# Ex-dates in the feed are occasionally a few sessions off — DOLPHIN's 2024-01-25
# filing moved the tape on 01-29, DRCSYSTEMS' by five days. Widening this is safe
# because it only decides which gaps are *considered*; the ratio still has to
# match one within VERIFY_TOLERANCE before anything is applied.
EX_DATE_SLACK = 7

SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("ex_date", pa.date32()),
    ("factor", pa.float64()),
    ("kind", pa.string()),          # bonus | split | bonus+split
    ("status", pa.string()),        # verified | unverified | no_bars | not_adjusting
    ("implied", pa.float64()),      # ratio the tape actually showed
    ("subject", pa.string()),       # raw label, kept for audit
    ("source_ex_date", pa.date32()),
    ("applied", pa.bool_()),
])

# ── Label parsing ────────────────────────────────────────────────────────────
# The feed writes the same two actions in two eras of house style, and both have
# to parse or a stock's oldest re-basing is silently never applied. Modern rows
# spell the action out ("Bonus 1:1", "Face Value Split (Sub-Division) - From
# Rs 10/- Per Share To Rs 2/- Per Share"). Rows from roughly 2007-2016 are
# abbreviated into a fixed-width field, losing the separators as they go:
# "Bon-1:1", "Fv Splt Frm Rs 10 To Rs 2", "Fv Spl-Rs10tore1/Bon-1:1".

# "Bonus 1:1", "Bonus 1 : 1", "Bonus 10:1", "Bonus - 1:2", "Bonus-1:3", "Bon-1:1".
# The ratio must follow the word directly: that is what keeps "Bonus Ncrps 4:1"
# and "Bonus Deb1:1" — preference shares and debentures, which leave the equity
# share count untouched — from reading as equity bonuses.
_BONUS_RE = re.compile(r"\bbon(?:us)?\s*-?\s*(\d+)\s*:\s*(\d+)")
# "Bonus Shares In The Ratio Of 1:1" — the same action with the ratio spelled
# out. Written as its own pattern rather than by loosening the one above, which
# would let "Bonus Ncrps 4:1" back in.
_BONUS_PHRASE_RE = re.compile(r"\bbonus\s+shares?\s+in\s+the\s+ratio\s+of\s+(\d+)\s*:\s*(\d+)")
# "From Rs 10/- Per Share To Rs 2/- Per Share", "Rs 10 To Re 1", "From Rs.5/- To
# Rs.1/-", and the abbreviated "Rs10tore1" with its spaces squeezed out.
# `from` is optional: the feed also writes bare "Face Value Split Rs 10 To Rs 1".
_SPLIT_RE = re.compile(
    r"(?:from\s*)?(?:rs|re)\.?\s*(\d+(?:\.\d+)?)\s*/?-?.*?to\s*(?:rs|re)?\.?\s*(\d+(?:\.\d+)?)",
)
# The token that declares a split, spelled out or abbreviated. A bare "Spl" is
# deliberately not enough on its own — the feed uses it for "Spl Dividend" far
# more often than for a split — so it counts only when the face-value pair
# follows it immediately, as in "Agm/Div-5%/Spl-Rs10tors2".
_SPLIT_KEY = re.compile(
    r"\bsplit\b|\bsplt\b|sub-?div|subdivision|\bfv\s*spl"
    r"|\bspl\w*\s*-\s*(?:rs|re)\.?\s*\d"
    r"|\bfv\s*[-.]?\s*(?:rs|re)\.?\s*\d"          # "Agm/Div-Rs.2/Fv Rs10tors5"
)
# Smallest ratio we treat as a real equity action. A 1:20 bonus is 1.05; the feed
# also carries oddities like "Bonus 1 : 1250" (k=1.0008) that are not equity
# bonuses at all and would otherwise slip through as no-op factors.
MIN_MEANINGFUL_FACTOR = 1.02
# Bonus issues that are not equity: debentures and preference shares leave the
# share count — and so the price — untouched.
_NOT_EQUITY_BONUS = re.compile(r"bonus\s*(?:deb|ncrps|ncd|ncrp|pref)")
# Events that re-base a price for real but derive no ratio from the filing:
# demergers, schemes of arrangement, and rights issues struck at a discount.
# They are recorded and never applied. Recording them is what lets the audit
# tell "we failed to apply a split" from "the company split itself in two" —
# without that, the two are indistinguishable from the price alone, and the
# only way to separate them is a hand-maintained list of exceptions.
_NON_ADJUSTING = re.compile(
    r"scheme|arrangement|demerg|de-merg|amalgamat|\brights\b|spin-?off|composite"
)


def non_adjusting_kind(subject: str) -> Optional[str]:
    """`scheme` | `rights` for a price-moving event with no derivable ratio."""
    s = " ".join(subject.lower().split())
    if not _NON_ADJUSTING.search(s):
        return None
    return "rights" if re.search(r"\brights\b", s) else "scheme"


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
        m = _BONUS_RE.search(s) or _BONUS_PHRASE_RE.search(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0 and a > 0:
                f = (a + b) / b
                if MIN_MEANINGFUL_FACTOR <= f <= MAX_FACTOR:
                    factor *= f
                    kinds.append("bonus")

    key = _SPLIT_KEY.search(s)
    if key:
        # From the keyword onward, deliberately. A label routinely names a
        # dividend before it names the split ("1st Interim Dividend Rs.2.50 Per
        # Share And Face Value Split From Rs.10/- To Re.1/-"), and a search from
        # the start of the string anchors on the dividend: that read KCP's 10-to-1
        # split as 2.5, and EMAMILTD's 2-to-1 as 6. Both then failed verification
        # against their own correct price gap and were never applied.
        m = _SPLIT_RE.search(s, key.start())
        if m:
            frm, to = float(m.group(1)), float(m.group(2))
            if to > 0 and frm > to:
                f = frm / to
                if 1.0 < f <= MAX_FACTOR:
                    factor *= f
                    kinds.append("split")

    # Bounded as an event, not as a filing: a label that carries both actions
    # ("Fv Spl-Rs10tore1/Bon-3:2" — a 10-to-1 split with a 3:2 bonus) compounds
    # to k=25, and clipping that product at the bound meant for one component
    # rejects the whole label rather than the half that overflowed.
    if not kinds or not 1.0 < factor <= MAX_EVENT_FACTOR:
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
                              min_f=MIN_GAP_FACTOR, max_f=MAX_GAP_FACTOR)
        ).fetchall()
        gap_by_symbol: dict = {}
        for sym, d, implied in gaps:
            gap_by_symbol.setdefault(sym, []).append((d, implied))
        for lst in gap_by_symbol.values():
            lst.sort()

        # Parse every filing into the *event* it belongs to, where an event is
        # one symbol on one ex-date. A bonus and a face-value split declared
        # effective the same day are filed as two separate rows and their
        # factors compound: AHCL's 2026-04-24 is "Bonus 1:1" alongside a 10-to-2
        # split, a 10x re-basing that neither row states on its own. Verifying
        # the rows one at a time rejects both — each is measured against the
        # full 10x gap and misses by a mile — and the stock then reads 88% below
        # an all-time high it is in fact sitting on.
        #
        # Keyed by normalized label, so a filing that is revised and
        # re-published — the same action stated twice — still counts once.
        events: Dict[Tuple[str, date], Dict[str, Tuple[float, str]]] = {}
        # Same keys, but the filings that move a price without stating a ratio.
        marks: Dict[Tuple[str, date], Dict[str, str]] = {}
        for year in range(start_year, end_year + 1):
            # Closed years never change, so they are served from the R2 cache;
            # the current year must be re-fetched or tonight's ex-dates are missed.
            want_fresh = refresh or (refresh_years is not None and year in refresh_years)
            try:
                data = fetch_year(year, refresh=want_fresh)
            except Exception as err:  # noqa: BLE001 — one bad year must not sink the build
                print(f"[actions] {year}: fetch failed ({err}) — skipped")
                continue

            year_filings = 0
            for r in data:
                subject = (r.get("subject") or "").strip()
                factor, kind = parse_factor(subject)
                mark = non_adjusting_kind(subject) if factor is None else None
                if factor is None and mark is None:
                    continue
                sym = (r.get("symbol") or "").strip()
                ex = _parse_ex_date(r.get("exDate") or "")
                if not sym or ex is None:
                    continue
                label = " ".join(subject.split())
                if factor is not None:
                    events.setdefault((sym, ex), {})[label] = (factor, kind)
                else:
                    marks.setdefault((sym, ex), {})[label] = mark or ""
                year_filings += 1
            print(f"[actions] {year}: {len(data):,} filings → {year_filings} split/bonus filings")

        rows: List[dict] = []
        for (sym, ex) in sorted(set(events) | set(marks)):
            filings = events.get((sym, ex), {})
            marked = marks.get((sym, ex), {})

            if not filings:
                # Price-moving, no ratio. Recorded so the audit can explain the
                # cliff this leaves in the adjusted history, never applied.
                rows.append({
                    "symbol": sym, "ex_date": ex, "factor": 1.0,
                    "kind": "+".join(sorted(set(marked.values()))),
                    "status": "not_adjusting", "implied": None,
                    "subject": " | ".join(marked),
                    "source_ex_date": ex, "applied": False,
                })
                continue

            factor = 1.0
            kinds: List[str] = []
            for f, k in filings.values():
                factor *= f
                kinds.extend(k.split("+"))
            kinds.extend(sorted(set(marked.values())))
            # A compounded event may legitimately exceed what one filing may
            # state, but not without limit — past MAX_EVENT_FACTOR the more
            # likely explanation is two unrelated filings colliding on a date.
            if not 1.0 < factor <= MAX_EVENT_FACTOR:
                continue
            kind = "+".join(sorted(set(kinds)))

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
                # Every label behind the event, so an audit can see both halves
                # of a compounded one rather than whichever arrived first.
                "subject": " | ".join(list(filings) + list(marked)),
                "source_ex_date": ex,
                "applied": status == "verified",
            })

        table = pa.Table.from_pylist(rows, schema=SCHEMA)
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
    WHERE applied AND factor BETWEEN {MIN_FACTOR} AND {MAX_EVENT_FACTOR}
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
