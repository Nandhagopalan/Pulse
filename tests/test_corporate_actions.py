"""
Two actions on one ex-date are one event, and they compound.

A company that declares a bonus and a face-value split effective the same day
appears in NSE's feed as two separate filings. Their factors multiply — AHCL's
2026-04-24 is "Bonus 1:1" alongside a 10-to-2 split, a 10x re-basing — but the
build treated same-symbol-same-ex-date as a duplicate filing and kept exactly
one of them. Both halves then failed verification for the same reason: each was
measured against the *full* 10x price gap and missed it by a mile, so the event
was recorded unverified and never applied.

The symptom is silent and points away from the cause. AHCL closed at a record
19.57 on 2026-09-11 and the terminal read it as 88.39% below an all-time high of
168.61 — a price that, unadjusted, it had genuinely traded at. Nothing looks
broken: the bars are faithful, the action was found, and the guard that refused
to apply it did exactly what it was written to do.

Across 19 years the feed carries 33 of these, and not one of them is the revised
filing the dedup was written for. The names are not obscure: BAJFINANCE,
BAJAJFINSV, NAZARA, 360ONE, CUPID, SARVESHWAR.

Covered here alongside it: the legacy abbreviated labels the parser used to miss
("Fv Spl-Rs10tore1/Bon-1:1"), which cost ~50 filings concentrated in 2007-2016,
and the factor bands, which used to be one pair doing three different jobs.
"""
from __future__ import annotations

from datetime import date

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.config import CURATED_DAILY
from pipeline.ingest import corporate_actions as ca

BARS = pa.schema([
    ("symbol", pa.string()), ("date", pa.date32()),
    ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()),
    ("close", pa.float64()), ("volume", pa.float64()), ("traded_value", pa.float64()),
])

SESSIONS = [date(2026, 4, d) for d in (20, 21, 22, 23, 24, 27, 28)]
EX_DATE = date(2026, 4, 24)          # SESSIONS[4]

SPLIT_10_TO_2 = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
SPLIT_10_TO_1 = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"

# symbol -> the closes it prints, and the filings the feed carries for it.
# Every re-basing below is deliberately a few percent off the stated ratio: the
# stock also moves on its own that day, which is the whole reason verification
# is a tolerance rather than an equality.
CASES = {
    # The AHCL shape. Two filings, k=2 and k=5, one 10x gap. Adjusted, the last
    # close is the record; raw, it is 89% below one.
    "COMPOUND": ([100.0, 102.0, 104.0, 106.0, 11.0, 11.5, 12.0],
                 ["Bonus 1:1", SPLIT_10_TO_2]),
    # The case the dedup was actually written for: one action, filed twice.
    # Counting it twice would state k=4 against a 2x gap and apply nothing.
    "REVISED": ([100.0, 102.0, 104.0, 106.0, 53.0, 54.0, 55.0],
                ["Bonus 1:1", "Bonus 1:1"]),
    # One filing, both actions, in the abbreviated style NSE used until ~2016.
    "LEGACY": ([200.0, 204.0, 208.0, 210.0, 11.0, 11.2, 11.4],
               ["Fv Spl-Rs10tore1/Bon-1:1"]),
    # SARVESHWAR's shape: k=30, past what any single filing may state.
    "BIG": ([300.0, 306.0, 312.0, 315.0, 10.7, 10.9, 11.0],
            ["Bonus 2:1", SPLIT_10_TO_1]),
}


@pytest.fixture
def lake(tmp_path, monkeypatch):
    """A lake and a feed wired so `ca.build` reads both from tmp_path."""
    rows = []
    for sym, (closes, _) in CASES.items():
        for d, c in zip(SESSIONS, closes):
            rows.append({"symbol": sym, "date": d, "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": 1e5,
                         "traded_value": c * 1e5})

    part = tmp_path / CURATED_DAILY / "2026"
    part.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=BARS), part / "data.parquet")

    monkeypatch.setattr(ca, "s3_uri", lambda key: str(tmp_path / key))
    monkeypatch.setattr(ca, "fetch_year", lambda year, refresh=False: [
        {"symbol": sym, "exDate": EX_DATE.strftime("%d-%b-%Y"), "subject": subject}
        for sym, (_, subjects) in CASES.items() for subject in subjects
    ])
    return duckdb.connect()


@pytest.fixture
def built(lake):
    """`{symbol: row}` from a full build over that lake."""
    table = ca.build(con=lake, start_year=2026, end_year=2026, write=False)
    return {r["symbol"]: r for r in table.to_pylist()}


# ── The event, not the filing ────────────────────────────────────────────────
def test_two_filings_on_one_ex_date_compound(built):
    """k=2 and k=5 declared the same day are one 10x event, not a duplicate."""
    row = built["COMPOUND"]
    assert row["factor"] == pytest.approx(10.0)
    assert row["kind"] == "bonus+split"
    assert row["applied"] is True


def test_the_compounded_event_is_what_gets_verified(built):
    """
    Either half alone is rejected — and must be, since neither explains the gap.
    Verification only lines up once the halves are multiplied first.
    """
    row = built["COMPOUND"]
    assert row["status"] == "verified"
    assert row["implied"] == pytest.approx(106.0 / 11.0, rel=1e-6)
    for half in (2.0, 5.0):
        assert abs(row["implied"] / half - 1.0) > ca.VERIFY_TOLERANCE


def test_both_labels_survive_for_audit(built):
    """A compounded event keeps every filing behind it, not whichever won."""
    assert built["COMPOUND"]["subject"] == f"Bonus 1:1 | {SPLIT_10_TO_2}"


def test_a_refiled_action_still_counts_once(built):
    """The same label twice is one action restated, so k stays 2 and applies."""
    row = built["REVISED"]
    assert row["factor"] == pytest.approx(2.0)
    assert row["applied"] is True


def test_one_row_per_symbol_and_ex_date(built):
    """Compounding replaces the dedup; it must not reintroduce duplicates."""
    assert len(built) == len(CASES)


# ── Bounds ───────────────────────────────────────────────────────────────────
def test_an_event_may_exceed_what_one_filing_may_state(built):
    """A 10-to-1 split with a 2:1 bonus is k=30, past the per-filing bound."""
    row = built["BIG"]
    assert row["factor"] == pytest.approx(30.0)
    assert row["applied"] is True


def test_the_tape_scan_is_wider_than_the_events_it_weighs():
    """
    A gap outside the scan band is never examined, so a band that only covered
    the events we expect would filter out the evidence for the largest of them.
    """
    assert ca.MAX_GAP_FACTOR >= ca.MAX_EVENT_FACTOR >= ca.MAX_FACTOR


# ── Label parsing ────────────────────────────────────────────────────────────
def test_the_abbreviated_style_parses(built):
    """NSE wrote "Fv Spl-Rs10tore1/Bon-1:1" for years; it is a 20x event."""
    row = built["LEGACY"]
    assert row["factor"] == pytest.approx(20.0)
    assert row["applied"] is True


@pytest.mark.parametrize(("subject", "factor"), [
    # Modern house style.
    ("Bonus 1:1", 2.0),
    (SPLIT_10_TO_2, 5.0),
    ("Bonus - 1:1 And Face Value Split From Rs. 10 To Rs. 2", 10.0),
    # Abbreviated, 2007-2016. The separators are whatever fit the field.
    ("Bonus - 1:1", 2.0),
    ("Bonus-1:3", 4.0 / 3.0),
    ("Bon 1:1/Fv Spl Rs.5tore.1", 10.0),
    ("Fv Splt Frm Rs 10 To Rs 2", 5.0),
    ("Fv Spl-Rs10tore1/Bon-3:2", 25.0),
    ("Agm/Div-5%/Spl-Rs10tors2 Purpose Revised", 5.0),
    # A 22:1 bonus is a real thing an issuer may declare (DPSCLTD, 2011-12-15).
    ("Bonus 22:1 And Face Value Split From Rs.10/- To Re.1/-", 230.0),
])
def test_parse_factor(subject, factor):
    assert ca.parse_factor(subject)[0] == pytest.approx(factor)


@pytest.mark.parametrize("subject", [
    # Bonus issues that are not equity leave the share count — and the price —
    # untouched. Loosening the bonus separator must not swallow these.
    "Sch Of Agmt- Bonus Deb1:1",
    "Scheme Of Arrangement - Bonus Ncrps 4:1",
    "Bonus Debentures 1:1",
    # "Spl" is the feed's abbreviation for "Special" far more often than for a
    # split, and a dividend does not re-base anything.
    "Spl Int Div-150%",
    "Div Int-Rs12.50+Spl-Rs16 Purpose Revised",
    "Agm/Div Fin-130% +Spl-20%",
    # Not an equity action at any size: k would be 1.0008.
    "Bonus 1 : 1250",
])
def test_parse_factor_declines(subject):
    assert ca.parse_factor(subject)[0] is None


# ── What the reader actually sees ────────────────────────────────────────────
def test_a_record_close_is_not_reported_as_88_percent_below_its_high(lake, built, tmp_path):
    """
    The bug as it reached the screen. COMPOUND's last close is its highest ever
    once the 10x is undone; left unadjusted it reads 89% below a high it set
    four sessions earlier in pre-split money.
    """
    actions = tmp_path / ca.ACTIONS_KEY
    actions.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(built.values()), schema=ca.SCHEMA), actions)

    cte = ca.adjusted_bars_cte(str(tmp_path / CURATED_DAILY / "*" / "data.parquet"), str(actions))
    ath, last = lake.execute(cte + """
        SELECT MAX(close), arg_max(close, date) FROM bars_adj WHERE symbol = 'COMPOUND'
    """).fetchone()

    assert last == pytest.approx(12.0)
    assert (last - ath) / ath * 100 == pytest.approx(0.0)


# ── The face value, not the dividend ─────────────────────────────────────────
@pytest.mark.parametrize(("subject", "factor"), [
    # The label names a dividend before it names the split, and a search from
    # the start of the string anchors on the dividend. KCP's 10-to-1 read as
    # 2.5, EMAMILTD's 2-to-1 as 6, GREAVESCOT's 5-to-1 not at all — each then
    # failed verification against its own correct price gap and was left
    # unapplied, then mistaken for a demerger when the audit listed what was
    # left over.
    ("1st Interim Dividend Rs.2.50 Per Share And Face Value Split From Rs.10/- To Re.1/-", 10.0),
    ("Interim Dividend Rs 2/- Per Share And Face Value Split From Rs 10/- To Rs 2/-", 5.0),
    ("Dividend Rs.6/- Per Share And Face Value Split From Rs.2/- To Re.1/-", 2.0),
    ("Interim Dividend Rs.3/- Per Share And Face Value Split From Rs.5/- To Rs.2/-", 2.5),
    # No "split" token at all — the face value pair follows "Fv" directly.
    ("Agm/Div-Rs.2/Fv Rs10tors5", 2.0),
    # The ratio spelled out rather than abutting the word.
    ("Bonus Shares In The Ratio Of 1:1", 2.0),
])
def test_the_split_is_read_from_the_keyword_not_the_start_of_the_label(subject, factor):
    assert ca.parse_factor(subject)[0] == pytest.approx(factor)


# ── Events that move a price without stating a ratio ─────────────────────────
@pytest.mark.parametrize(("subject", "kind"), [
    ("Demerger", "scheme"),
    ("Scheme Of Arrangement", "scheme"),
    ("Composite Scheme Of Arrangement And Amalgamation", "scheme"),
    ("Rights 1:1 @ Premium Rs 0/-", "rights"),
    ("Rights 3:2 @ Premium Rs 15/-", "rights"),
])
def test_non_adjusting_events_are_recognised(subject, kind):
    """
    Recorded, never applied. Knowing a demerger happened is what lets the audit
    explain the cliff it leaves instead of demanding a hand-written exception.
    """
    assert ca.non_adjusting_kind(subject) == kind
    assert ca.parse_factor(subject)[0] is None


@pytest.mark.parametrize("subject", ["Bonus 1:1", "Annual General Meeting", "Dividend Rs 2/-"])
def test_ordinary_filings_are_not_marked_non_adjusting(subject):
    assert ca.non_adjusting_kind(subject) is None
