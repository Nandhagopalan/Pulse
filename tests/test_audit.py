"""
The check that asks the question the rest of the pipeline does not.

Everywhere else verifies inputs — "does the ratio NSE stated match the tape?" —
and answers a disagreement by applying nothing. That is safe and it is silent,
and the two together are how AHCL spent five months reported as 88% below an
all-time high it was sitting on: a bonus and a split filed for the same day were
read as one duplicated filing, each half was measured against the full 10x gap,
both were rejected, and the resulting number — a stock well off its high — looked
like every other stock well off its high.

This audit asks the outcome instead: does the adjusted history still contain
cliffs that nothing explains? A symbol whose close falls 44% in a session and
stays there has had an action go unapplied, whatever the feed does or does not
say about it.

What is pinned here is mostly the *grading*, because an audit that cries wolf is
an audit nobody reads — which is the failure it exists to prevent. Fund units
are excluded by rule, reviewed demergers by a file someone has to edit, and
everything left over fails the run.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline import audit
from pipeline.config import CURATED_DAILY
from pipeline.ingest import backfill
from pipeline.ingest import corporate_actions as ca

BARS = pa.schema([
    ("symbol", pa.string()), ("date", pa.date32()), ("isin", pa.string()),
    ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()),
    ("close", pa.float64()), ("volume", pa.float64()), ("traded_value", pa.float64()),
])

SESSIONS = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9)]
CLIFF = 4                                    # index of the re-basing session
STALE = [d - timedelta(days=400) for d in SESSIONS]

# symbol -> (isin, closes, last-traded sessions)
# Each cliff is a 10x drop, far past RESIDUAL_FALL, and none has an action
# behind it — the actions file below is empty. What separates them is how the
# audit is expected to grade them.
CASES = {
    "CLEAN":    ("INE001", [100, 101, 102, 103, 104, 105, 106], SESSIONS),
    "UNAPPLIED": ("INE002", [100, 101, 102, 103, 10.3, 10.4, 10.5], SESSIONS),
    "DEMERGED": ("INE003", [200, 201, 202, 203, 20.3, 20.4, 20.5], SESSIONS),
    "GOLDFUND": ("INF004", [4000, 4010, 4020, 4030, 40.3, 40.4, 40.5], SESSIONS),
    "DELISTED": ("INE005", [300, 301, 302, 303, 30.3, 30.4, 30.5], STALE),
    # A demerger the feed states outright — explained without anyone listing it.
    "SPLITCO":  ("INE006", [400, 401, 402, 403, 40.3, 40.4, 40.5], SESSIONS),
    # Doubles and comes straight back: a trade, not a re-basing.
    "ROUNDTRIP": ("INE007", [50, 51, 52, 53, 26.0, 51.0, 52.0], SESSIONS),
    # Penny stock on the tick floor, where one tick is 100%.
    "TICKER":   ("INE008", [0.10, 0.10, 0.10, 0.10, 0.05, 0.05, 0.05], SESSIONS),
}


@pytest.fixture
def lake(tmp_path, monkeypatch):
    rows = []
    for sym, (isin, closes, sessions) in CASES.items():
        for d, c in zip(sessions, closes):
            rows.append({"symbol": sym, "date": d, "isin": isin, "open": c,
                         "high": c * 1.01, "low": c * 0.99, "close": float(c),
                         "volume": 1e5, "traded_value": c * 1e5})

    for year in {d.year for _, (_, _, ss) in CASES.items() for d in ss}:
        part = tmp_path / CURATED_DAILY / str(year)
        part.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist([r for r in rows if r["date"].year == year], schema=BARS),
            part / "data.parquet",
        )

    # One recorded-but-never-applied demerger; nothing else has a filing, so the
    # grading is what decides the rest.
    acts = tmp_path / ca.ACTIONS_KEY
    acts.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{
        "symbol": "SPLITCO", "ex_date": SESSIONS[CLIFF], "factor": 1.0,
        "kind": "scheme", "status": "not_adjusting", "implied": None,
        "subject": "Demerger", "source_ex_date": SESSIONS[CLIFF], "applied": False,
    }], schema=ca.SCHEMA), acts)

    baseline = tmp_path / "accepted.json"
    baseline.write_text(json.dumps({"accepted": [
        {"symbol": "DEMERGED", "date": SESSIONS[CLIFF].isoformat()},
    ]}), encoding="utf8")

    monkeypatch.setattr(backfill, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(backfill, "USE_R2", False)
    monkeypatch.setattr(audit, "ACCEPTED_PATH", baseline)
    return duckdb.connect()


@pytest.fixture
def graded(lake):
    return {f.symbol: f for f in audit.residuals(lake)}


# ── Grading ──────────────────────────────────────────────────────────────────
def test_an_unexplained_cliff_is_new(graded):
    """The AHCL shape: a 10x re-basing with no action behind it."""
    assert graded["UNAPPLIED"].verdict == "new"
    assert graded["UNAPPLIED"].implied == pytest.approx(103 / 10.3)


def test_a_reviewed_demerger_is_accepted(graded):
    """Someone looked at this one and wrote it down, so it stops being news."""
    assert graded["DEMERGED"].verdict == "accepted"


def test_a_fund_unit_is_excluded_by_rule(graded):
    """
    NSE's corporate action feed is an equity feed and carries no unit splits, so
    a gold ETF's ~100x re-basing has no filing to find and never will. Keyed on
    the ISIN prefix, not the series — an ETF trades in the same series as a share.
    """
    assert graded["GOLDFUND"].verdict == "fund unit"


def test_a_symbol_that_stopped_trading_is_not_reported(graded):
    """A cliff nobody will ever look at is not worth failing a run over."""
    assert "DELISTED" not in graded


def test_a_clean_symbol_is_not_reported(graded):
    assert "CLEAN" not in graded


# ── What the nightly chain does with it ──────────────────────────────────────
def test_strict_mode_raises_on_anything_new(lake):
    """
    The point of the whole module. A finding that prints and returns lands where
    the `unverified` bucket landed; raising is what fails the scheduled workflow
    and sends mail.
    """
    with pytest.raises(audit.AuditFailed, match="UNAPPLIED"):
        audit.run(lake, strict=True)


def test_warn_mode_returns_the_findings_instead(lake):
    new = audit.run(lake, strict=False)
    assert [f.symbol for f in new] == ["UNAPPLIED"]


def test_accepted_and_fund_units_alone_do_not_fail_a_run(lake, tmp_path, monkeypatch):
    """
    Steady state has 128 residuals and must still exit 0, or the mail gets
    filtered and the next real finding goes unread.
    """
    baseline = tmp_path / "accepted.json"
    baseline.write_text(json.dumps({"accepted": [
        {"symbol": s, "date": SESSIONS[CLIFF].isoformat()} for s in ("DEMERGED", "UNAPPLIED")
    ]}), encoding="utf8")
    monkeypatch.setattr(audit, "ACCEPTED_PATH", baseline)

    assert audit.run(lake, strict=True) == []


def test_a_missing_baseline_file_is_not_a_crash(lake, tmp_path, monkeypatch):
    """A fresh checkout without the file audits everything rather than dying."""
    monkeypatch.setattr(audit, "ACCEPTED_PATH", tmp_path / "nope.json")
    assert audit.accepted() == set()
    with pytest.raises(audit.AuditFailed):
        audit.run(lake, strict=True)


# ── Explained from the data, not from a list ─────────────────────────────────
def test_a_filed_demerger_explains_its_own_cliff(graded):
    """
    The whole reason accepted_residuals.json shrank from 78 rows to 7. A rule has
    to point at the filing that explains the cliff, so it cannot quietly swallow
    an unapplied split the way a hand-written list did — eleven of those 78 were
    real splits (KCP, GREAVESCOT, MANAPPURAM) suppressed as demergers.
    """
    assert graded["SPLITCO"].verdict == "scheme"


def test_a_move_that_comes_back_is_not_a_re_basing(graded):
    """No corporate action has ever undone itself a week later."""
    assert "ROUNDTRIP" not in graded


def test_a_price_below_the_tick_floor_is_not_reported(graded):
    """At 5 paise a single tick is 100%, and every leg of it reads as a split."""
    assert "TICKER" not in graded


def test_only_the_genuinely_unexplained_reaches_the_baseline_file(lake):
    """
    Rules run first; the file is the remainder, not the front line. Here only
    UNAPPLIED and DEMERGED survive them, and DEMERGED is the one in the file.
    """
    new = audit.run(lake, strict=False)
    assert [f.symbol for f in new] == ["UNAPPLIED"]
