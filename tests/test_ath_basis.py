"""
Pin all-time and 52-week highs to a closing basis.

The bug this guards against reads as a data error and is not one. Every
distance the snapshot publishes is measured from the latest close, so a peak
taken from intraday highs is a level the close can never reach: a stock that
breaks out and gives back part of the move reports itself as trading below an
all-time high it set hours earlier. INDSWFTLAB closed at a record 388.55 on
2026-09-09 and read -3.82% against its own 404.00 spike from that session.

The distortion is silent and one-directional — MAX(close) <= MAX(high), so the
intraday basis only ever overstates the distance — which is why nothing further
downstream notices. It also loses the ATH tag entirely, since `isATH` wants the
close within 0.4% of the peak.
"""
from __future__ import annotations

from datetime import date

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.compute import analytics
from pipeline.config import CURATED_DAILY
from pipeline.ingest import backfill
from pipeline.ingest import corporate_actions as ca

BARS = pa.schema([
    ("symbol", pa.string()), ("date", pa.date32()),
    ("isin", pa.string()), ("series", pa.string()),
    ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()),
    ("close", pa.float64()), ("volume", pa.float64()), ("traded_value", pa.float64()),
])


def _bar(sym, d, high, close):
    return {"symbol": sym, "date": d, "isin": f"INE{sym[:6]:X<6}01010", "series": "EQ",
            "open": close, "high": high,
            "low": close * 0.98, "close": close, "volume": 1e5,
            "traded_value": close * 1e5}


@pytest.fixture
def lake(tmp_path, monkeypatch):
    """
    A three-symbol lake, written where `--local --no-r2` mode looks for it.

    SPIKER is the INDSWFTLAB shape: a record close on the final session, under
    an intraday high from that same session. FADER peaked intraday long ago and
    has never closed there. SPLITTER carries a 2:1 split, so the adjustment
    join stays exercised rather than short-circuited by an empty actions file.
    """
    rows = []
    sessions = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9)]
    for i, d in enumerate(sessions):
        rows.append(_bar("SPIKER", d, high=300.0 + i * 10, close=290.0 + i * 10))
        rows.append(_bar("FADER", d, high=500.0 if i == 0 else 150.0 + i, close=150.0 + i))
        # Raw closes halve on the 2026-09-07 ex-date; adjustment must undo it.
        raw = 400.0 + i * 4 if d < date(2026, 9, 7) else (400.0 + i * 4) / 2
        rows.append(_bar("SPLITTER", d, high=raw * 1.05, close=raw))
    # SPIKER's own final-session spike: the high the close is measured against.
    rows[-3]["high"] = 404.0

    part = tmp_path / CURATED_DAILY / "2026"
    part.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=BARS), part / "data.parquet")

    acts = tmp_path / ca.ACTIONS_KEY
    acts.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{
        "symbol": "SPLITTER", "ex_date": date(2026, 9, 7), "factor": 2.0,
        "kind": "split", "status": "verified", "implied": 2.0,
        "subject": "Rs 10 To Rs 5", "source_ex_date": date(2026, 9, 7), "applied": True,
    }], schema=ca.SCHEMA), acts)

    monkeypatch.setattr(backfill, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(backfill, "USE_R2", False)
    return duckdb.connect()


def test_ath_is_the_record_close_not_the_intraday_spike(lake):
    rec = analytics._load_aths(lake)["SPIKER"]
    assert rec["close"] == pytest.approx(350.0)   # the final close, not the 404.00 high
    assert rec["date"] == "2026-09-09"


def test_a_record_close_reads_as_zero_from_ath(lake):
    """The whole point: no gap between a record close and the ATH it just set."""
    rec = analytics._load_aths(lake)["SPIKER"]
    price = 350.0
    assert (rec["close"] - price) / rec["close"] * 100 == pytest.approx(0.0)


def test_a_stale_intraday_peak_does_not_become_the_ath(lake):
    """FADER printed 500.00 once and never closed above 156.00."""
    assert analytics._load_aths(lake)["FADER"]["close"] == pytest.approx(156.0)


def test_the_ath_stays_split_adjusted(lake):
    """
    SPLITTER's pre-split closes are halved, so the peak is the 2026-09-09 close
    of 212.00 rather than the raw 412.00 print from before the ex-date.
    """
    rec = analytics._load_aths(lake)["SPLITTER"]
    assert rec["close"] == pytest.approx(212.0)
    assert rec["date"] == "2026-09-09"


def test_the_closing_basis_can_only_narrow_the_distance(lake):
    """
    MAX(close) <= MAX(high) for every symbol, so switching bases never reports
    a stock as further from its high than the intraday basis did. A regression
    here is the old bug returning.
    """
    cte = ca.adjusted_bars_cte(backfill.daily_glob(), str(backfill.LOCAL_ROOT / ca.ACTIONS_KEY))
    rows = lake.execute(
        cte + "SELECT symbol, MAX(close), MAX(high) FROM bars_adj GROUP BY symbol"
    ).fetchall()
    assert rows
    for sym, ath_close, ath_high in rows:
        assert ath_close <= ath_high + 1e-9, sym
