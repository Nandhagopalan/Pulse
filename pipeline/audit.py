"""
Output-side audit of the adjusted price history.

Everything else in this pipeline checks its *inputs*: `corporate_actions.build`
asks "does the ratio NSE stated match what the tape did?" and refuses to apply
an event that disagrees. That is the right question, and it is not sufficient.
It cannot see an action that was never filed, never parsed, or split across two
filings that each look wrong alone — and in every one of those cases the answer
is to apply nothing, which is indistinguishable from there being nothing to do.

This module asks the opposite question, of the finished product: **does the
adjusted history still contain cliffs that nothing explains?** A symbol whose
close falls 44% in one session and stays there has almost certainly had a split
or bonus go unapplied. Asking it takes one scan and grades the whole universe at
once, which is how AHCL's April re-basing should have surfaced in April rather
than five months later, after it had spent that whole time reported as 88% below
an all-time high it was in fact sitting on.

Every cliff is explained from data wherever that is possible, and only what
survives is reported:

  scheme      The feed says a demerger or scheme of arrangement happened here.
  rights      A rights issue struck below market, which re-bases the price for
              real. Both are recorded by `corporate_actions` and never applied,
              because no ratio is derivable from the filing.
  fund unit   ETF and mutual-fund units (ISIN `INF…`). NSE's corporate action
              feed is an *equity* feed and carries no unit splits, so a ~100x
              re-basing on GOLDBEES has no filing to find.
  accepted    What is left after all of that, reviewed by a person and written
              down in accepted_residuals.json.
  new         Everything else. This is the alarm.

Deriving the first three rather than listing them is not tidiness. The list came
first, and it was actively dangerous: eleven of its seventy-eight entries were
real unapplied splits — KCP's 10-to-1, GREAVESCOT's 5-to-1, MANAPPURAM's 1:1
bonus — that had been mistaken for demergers and would have been suppressed in
perpetuity by the very file meant to reduce noise. A rule cannot make that
mistake, because it has to point at the filing that explains the cliff. What
remains in the file is seven cases that genuinely resist explanation, which is
a number a person can actually review.

`strict` mode raises on anything new, which is what makes the nightly run fail
and send mail. A finding that prints and does not fail is a finding nobody
reads — that is precisely how the `unverified` bucket hid AHCL.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, NamedTuple, Optional, Set, Tuple

from .config import s3_uri
from .ingest import backfill
from .ingest import corporate_actions as ca
from .sources import r2

ACCEPTED_PATH = Path(__file__).resolve().parent / "accepted_residuals.json"

# A one-session move this large, that the next session does not undo, is a
# re-basing rather than a trade. 1.8 is roughly -44%; 0.55 is roughly +82%.
# Below this the audit would be reading circuit moves and crash days.
RESIDUAL_FALL = 1.8
RESIDUAL_RISE = 0.55
# Only symbols that traded this recently matter: a cliff in a delisted stock's
# history is nothing anyone will ever read.
LIVE_WITHIN_DAYS = 7
# A gap across a hole in the history is a hole, not an action.
MAX_SESSION_GAP = 15
# Below this the tick size swamps the percentage. NSE quotes in paise, so a
# share at 5p moves 100% by ticking once — BLUECHIP oscillates between 0.05 and
# 0.10 for years and every leg of it reads as a re-basing.
MIN_PRICE = 1.0


class Finding(NamedTuple):
    symbol: str
    date: str
    prev_close: float
    close: float
    implied: float
    isin: str
    verdict: str        # scheme | rights | fund unit | accepted | new

    def line(self) -> str:
        return (f"{self.symbol:<14} {self.date}  {self.prev_close:>10.2f} -> "
                f"{self.close:>9.2f}   k={self.implied:>8.3f}")


class AuditFailed(RuntimeError):
    """Raised in strict mode when the audit finds something unexplained."""


def _con():
    if backfill.LOCAL_ROOT is not None and not backfill.USE_R2:
        import duckdb
        return duckdb.connect()
    return r2.duck()


def _actions_uri() -> str:
    if backfill.LOCAL_ROOT is not None and not backfill.USE_R2:
        return str(backfill.LOCAL_ROOT / ca.ACTIONS_KEY)
    return s3_uri(ca.ACTIONS_KEY)


def accepted() -> Set[Tuple[str, str]]:
    """The (symbol, date) residuals a human has already looked at."""
    if not ACCEPTED_PATH.exists():
        return set()
    doc = json.loads(ACCEPTED_PATH.read_text(encoding="utf8"))
    return {(r["symbol"], r["date"]) for r in doc.get("accepted", [])}


# How far ahead to look for the move coming back. A re-basing is permanent; a
# doubling that halves again a few sessions later was a trade, not a split.
REVERSAL_WINDOW = 10
REVERSAL_TOLERANCE = 0.25

RESIDUAL_SQL = """
, gaps AS (
    SELECT symbol, date, close,
           LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close,
           LAG(date)  OVER (PARTITION BY symbol ORDER BY date) AS prev_date,
           -- Where the price ranges either side, to tell a re-basing from a round
           -- trip: BLUECHIP doubles and halves back within the month, and no
           -- corporate action has ever undone itself.
           MAX(close) OVER (PARTITION BY symbol ORDER BY date
                            ROWS BETWEEN 1 FOLLOWING AND {reversal} FOLLOWING) AS fwd_high,
           MIN(close) OVER (PARTITION BY symbol ORDER BY date
                            ROWS BETWEEN 1 FOLLOWING AND {reversal} FOLLOWING) AS fwd_low,
           MAX(close) OVER (PARTITION BY symbol ORDER BY date
                            ROWS BETWEEN {reversal} PRECEDING AND 1 PRECEDING) AS bwd_high,
           MIN(close) OVER (PARTITION BY symbol ORDER BY date
                            ROWS BETWEEN {reversal} PRECEDING AND 1 PRECEDING) AS bwd_low
    FROM bars_adj
),
live AS (
    SELECT symbol FROM bars_adj GROUP BY symbol
    HAVING MAX(date) >= (SELECT MAX(date) FROM bars_adj) - {live_days}
),
ident AS (
    SELECT symbol, arg_max(isin, date) AS isin
    FROM read_parquet('{daily_glob}') GROUP BY symbol
)
SELECT g.symbol, g.date, g.prev_close, g.close,
       g.prev_close / g.close AS implied, COALESCE(i.isin, '') AS isin,
       COALESCE(s.kind, '') AS event_kind
FROM gaps g
JOIN live l ON l.symbol = g.symbol
LEFT JOIN ident i ON i.symbol = g.symbol
-- Any filing near this session that moves a price without stating a ratio.
LEFT JOIN LATERAL (
    SELECT a.kind FROM read_parquet('{actions_uri}') a
    WHERE a.symbol = g.symbol
      AND NOT a.applied
      AND (a.kind LIKE '%scheme%' OR a.kind LIKE '%rights%')
      AND abs(datediff('day', a.ex_date, g.date)) <= {slack}
    LIMIT 1
) s ON TRUE
WHERE g.prev_close IS NOT NULL
  AND g.date - g.prev_date <= {max_gap}
  AND g.close >= {min_price} AND g.prev_close >= {min_price}
  AND (g.prev_close / g.close >= {fall} OR g.prev_close / g.close <= {rise})
  -- Drop round trips, both legs of them. A re-basing is permanent, so the price
  -- neither comes back to where it was (the fall) nor arrives from where it is
  -- returning to (the rebound). Testing only the fall leaves the rebound to be
  -- reported on its own, as its own apparent re-basing in the other direction.
  -- COALESCE, not IS NOT NULL: at either end of a symbol's history the window is
  -- empty, and a NULL here would silently drop the row instead of keeping it.
  AND NOT COALESCE(g.fwd_high >= g.prev_close * (1 - {rev_tol})
                   AND g.fwd_low <= g.prev_close * (1 + {rev_tol}), FALSE)
  AND NOT COALESCE(g.bwd_high >= g.close * (1 - {rev_tol})
                   AND g.bwd_low <= g.close * (1 + {rev_tol}), FALSE)
ORDER BY g.prev_close / g.close DESC
"""


def residuals(con=None) -> List[Finding]:
    """Every unexplained re-basing left in the adjusted history, graded."""
    own = con is None
    con = con or _con()
    try:
        daily_glob = backfill.daily_glob()
        cte = ca.adjusted_bars_cte(daily_glob, _actions_uri())
        rows = con.execute(cte + RESIDUAL_SQL.format(
            daily_glob=daily_glob, actions_uri=_actions_uri(), live_days=LIVE_WITHIN_DAYS,
            max_gap=MAX_SESSION_GAP, fall=RESIDUAL_FALL, rise=RESIDUAL_RISE,
            reversal=REVERSAL_WINDOW, rev_tol=REVERSAL_TOLERANCE, slack=ca.EX_DATE_SLACK,
            min_price=MIN_PRICE,
        )).fetchall()
    finally:
        if own:
            con.close()

    known = accepted()
    out: List[Finding] = []
    for sym, d, prev_close, close, implied, isin, event_kind in rows:
        iso = d.isoformat()
        # ISIN, not series: an ETF trades in the same series as a share, and it
        # is the INF prefix that says "this is a fund unit, and NSE's equity
        # action feed was never going to mention it".
        if isin.startswith("INF"):
            verdict = "fund unit"
        elif event_kind:
            # The feed says a demerger, scheme or rights issue happened here, so
            # the cliff is explained by something with no ratio to apply.
            verdict = event_kind
        elif (sym, iso) in known:
            verdict = "accepted"
        else:
            verdict = "new"
        out.append(Finding(sym, iso, prev_close, close, implied, isin, verdict))
    return out


def unverified_live(con=None) -> List[Tuple[str, str, float, float, str]]:
    """
    Events we found, believed, and then refused to apply, on symbols still
    trading. The narrower of the two checks and the more direct: this is the
    exact shape AHCL had for five months.
    """
    own = con is None
    con = con or _con()
    try:
        return con.execute(f"""
            WITH live AS (
                SELECT symbol, MAX(date) AS last_date
                FROM read_parquet('{backfill.daily_glob()}')
                GROUP BY symbol
            )
            SELECT a.symbol, CAST(a.ex_date AS VARCHAR), a.factor, a.implied, a.subject
            FROM read_parquet('{_actions_uri()}') a
            JOIN live l ON l.symbol = a.symbol
            WHERE a.status = 'unverified'
              AND l.last_date >= (SELECT MAX(last_date) FROM live) - {LIVE_WITHIN_DAYS}
            ORDER BY a.ex_date DESC
        """).fetchall()
    finally:
        if own:
            con.close()


def run(con=None, strict: bool = True, limit: int = 40) -> List[Finding]:
    """
    Report the audit, and in strict mode raise if anything is unexplained.

    Returns the new findings so a caller that wants to record them can, and
    raises afterwards rather than instead — the nightly chain logs the detail to
    `ingest_log` before letting the failure out.
    """
    own = con is None
    con = con or _con()
    try:
        found = residuals(con)
        stale = unverified_live(con)
    finally:
        if own:
            con.close()

    buckets: dict = {}
    for f in found:
        buckets.setdefault(f.verdict, []).append(f)
    print(f"[audit] {len(found)} residual re-basings — "
          + ", ".join(f"{len(v)} {k}" for k, v in sorted(buckets.items())))

    if stale:
        print(f"[audit] {len(stale)} action(s) found but not applied, on symbols still trading:")
        for sym, ex, factor, implied, subject in stale[:limit]:
            shown = f"{implied:.3f}" if implied is not None else "no gap"
            print(f"    {sym:<14} {ex}  stated k={factor:<8.3f} tape={shown:<9} {subject[:60]}")

    new = buckets.get("new", [])
    if new:
        print(f"[audit] UNEXPLAINED — {len(new)} re-basing(s) with no action behind them:")
        for f in new[:limit]:
            print("   ", f.line())
        if len(new) > limit:
            print(f"    … {len(new) - limit} more")
        print("[audit] Each is a split or bonus the pipeline did not apply, or a "
              "demerger to record in pipeline/accepted_residuals.json.")
        if strict:
            raise AuditFailed(
                f"{len(new)} unexplained re-basing(s): "
                + ", ".join(f"{f.symbol}@{f.date}" for f in new[:10])
            )
    else:
        print("[audit] no unexplained re-basings")
    return new


def rebuild_accepted(con=None, note: Optional[str] = None) -> int:
    """
    Rewrite accepted_residuals.json from what the lake shows today.

    Deliberately not wired into any chain: accepting a finding is a judgement a
    person makes, and it should arrive as a reviewable diff rather than as a
    side effect of a run that was failing.
    """
    found = [f for f in residuals(con) if f.verdict in ("new", "accepted")]
    doc = {
        "note": note or (
            "Re-basings that survive every rule the audit can apply: not a fund "
            "unit, no demerger, scheme or rights issue filed anywhere near the "
            "date, and not a move the next fortnight undoes. Each was reviewed "
            "by hand and left unadjusted. Keep this list short — it suppresses "
            "an alarm, and an entry added carelessly hides a real unapplied "
            "action for good, which is exactly what happened when this file was "
            "a 78-row snapshot instead of a reviewed remainder."
        ),
        "accepted": [{"symbol": f.symbol, "date": f.date, "implied": round(f.implied, 4)}
                     for f in sorted(found, key=lambda x: (x.symbol, x.date))],
    }
    ACCEPTED_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf8")
    return len(doc["accepted"])
