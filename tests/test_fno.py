"""
The F&O bhavcopy, both layouts, parsed into one row shape.

The traps these pin down are all silent. Volume is in contracts but open
interest is in units, in both layouts; mixing them makes every put-call ratio
and OI-to-volume figure wrong by the lot size. The legacy file has no lot size,
so it has to be recovered from notional turnover, and lots can differ between
expiries trading on the same day. And FOVOLT only started carrying prices in
April 2011: an earlier file must yield no spot, not a volatility number read as
a price.

Rows are real lines from NSE files (NIFTY, 14 Jun 2011 and 11 Sep 2026) unless
marked otherwise.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest

from pipeline.config import config
from pipeline.sources import nse

LEGACY_HEADER = ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,"
                 "CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,")
UDIFF_HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
                "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
                "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
                "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bhav.csv", text)
    return buf.getvalue()


def test_legacy_rows_keep_units_and_recover_the_lot():
    lines = [
        LEGACY_HEADER,
        # Futures turnover chosen so turnover / (close x contracts) = 50.02, as NSE's is.
        "FUTIDX,NIFTY,30-Jun-2011,0,XX,5480,5530,5470,5515.8,5515.8,254909,703000.00,23010750,-1000,14-JUN-2011,",
        "OPTIDX,NIFTY,28-Jul-2011,5500,CE,147,156,136.85,144.6,144.6,6531,18439.43,1248400,47250,14-JUN-2011,",
        "OPTIDX,NIFTY,28-Jul-2011,5500,PE,119.7,128,111.2,118,118,8853,24869.91,941900,68300,14-JUN-2011,",
        "OPTIDX,NIFTY,25-Aug-2011,6500,CE,0,0,0,3.1,3.1,0,0,0,0,14-JUN-2011,",
        "OPTSTK,RELIANCE,30-Jun-2011,900,CE,20,21,19,20.5,20.5,100,900.0,5000,0,14-JUN-2011,",
    ]
    rows = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2011, 6, 14), ["NIFTY"])

    assert [r["instrument"] for r in rows] == ["FUT", "OPT", "OPT", "OPT"]  # stock options excluded
    fut, ce, pe, untraded = rows
    assert fut["strike"] is None and fut["option_type"] is None
    assert fut["contracts"] == 254909 and fut["oi"] == 23010750      # contracts vs units, as published
    assert fut["lot_size"] == 50
    assert ce["expiry"] == date(2011, 7, 28) and ce["strike"] == 5500 and ce["option_type"] == "CE"
    assert ce["turnover"] == pytest.approx(18439.43e5)
    assert ce["lot_size"] == 50 and pe["lot_size"] == 50              # recovered from strike + premium
    assert untraded["close"] == 3.1 and untraded["contracts"] == 0
    assert untraded["lot_size"] is None                              # no trade, no estimate: filled per contract later


def test_legacy_lots_are_per_expiry_when_sizes_overlap():
    """Illustrative: a lot revision where the near month still trades at the old size."""
    lines = [
        LEGACY_HEADER,
        "FUTIDX,NIFTY,25-Feb-2021,0,XX,15000,15100,14950,15000,15000,1000,7500.00,0,0,01-FEB-2021,",
        "FUTIDX,NIFTY,25-Mar-2021,0,XX,15050,15150,15000,15050,15050,1000,7525.00,0,0,01-FEB-2021,",
    ]
    near, far = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2021, 2, 1), ["NIFTY"])
    assert near["lot_size"] == 50 and far["lot_size"] == 50

    lines[2] = "FUTIDX,NIFTY,25-Mar-2021,0,XX,15050,15150,15000,15050,15050,1000,11287.50,0,0,01-FEB-2021,"
    near, far = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2021, 2, 1), ["NIFTY"])
    assert near["lot_size"] == 50 and far["lot_size"] == 75


def test_thin_far_expiry_with_a_stale_close_still_gets_the_real_lot():
    """
    Real 2022 case: NIFTY's 08-Dec weekly, seven weeks out, traded 38 contracts of
    the 18100 PE at prices far from its ₹400 close. Alone that row implies 50.9,
    which rounded to 51; pooled and snapped to the liquid future it is 50.
    """
    lines = [
        LEGACY_HEADER,
        "FUTIDX,NIFTY,27-Oct-2022,0,XX,17550,17650,17500,17600,17600,100000,880000.00,1000000,0,21-OCT-2022,",
        "OPTIDX,NIFTY,08-Dec-2022,18100,PE,400,400,400,400,400,38,357.80,1900,1900,21-OCT-2022,",
        "OPTIDX,NIFTY,08-Dec-2022,18500,PE,1500,1500,1500,1500,1500,28,272.58,1400,1400,21-OCT-2022,",
    ]
    fut, pe1, pe2 = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2022, 10, 21), ["NIFTY"])
    assert nse.lot_from_turnover(pe1) == 51        # the single-row estimate that went wrong
    assert fut["lot_size"] == pe1["lot_size"] == pe2["lot_size"] == 50


def test_volatile_session_lot_comes_from_options_not_futures():
    """
    2022-02-24, NIFTY down ~5%: the near future's turnover implied 50.78 because it
    traded far from its close. Option notionals are mostly strike, so a liquid
    expiry's options still establish 50, and the futures-only expiry snaps to it.
    """
    lines = [
        LEGACY_HEADER,
        "FUTIDX,NIFTY,31-Mar-2022,0,XX,16800,16850,16200,16262.5,16262.5,303043,2502568.61,1000000,0,24-FEB-2022,",
    ]
    for i in range(12):
        strike, premium = 16000 + 50 * i, 150.0
        lakh = 50 * (strike + premium) * 1000 / 1e5
        lines.append(f"OPTIDX,NIFTY,24-Feb-2022,{strike},CE,160,200,100,{premium},{premium},1000,{lakh:.2f},50000,0,24-FEB-2022,")
    rows = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2022, 2, 24), ["NIFTY"])
    fut = rows[0]
    assert nse.lot_from_turnover(fut) == 51        # what trusting the future produced
    assert {r["lot_size"] for r in rows} == {50}


def test_lots_differ_by_expiry_when_a_revision_is_in_flight():
    """Nov 2014 - Oct 2015: NIFTY near months in lots of 25, long-dated options still 50."""
    lines = [LEGACY_HEADER]
    for i in range(12):
        strike = 8400 + 50 * i
        near = 25 * (strike + 60) * 2000 / 1e5
        lines.append(f"OPTIDX,NIFTY,26-Mar-2015,{strike},CE,60,70,50,60,60,2000,{near:.2f},100000,0,05-MAR-2015,")
    far = 50 * (9000 + 400) * 20 / 1e5
    lines.append(f"OPTIDX,NIFTY,24-Dec-2015,9000,CE,400,400,400,400,400,20,{far:.2f},5000,0,05-MAR-2015,")
    rows = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2015, 3, 5), ["NIFTY"])
    assert {r["lot_size"] for r in rows if r["expiry"] == date(2015, 3, 26)} == {25}
    assert [r["lot_size"] for r in rows if r["expiry"] == date(2015, 12, 24)] == [50]  # 100% off 25: not snapped


def test_untraded_days_take_the_contracts_own_lot():
    import pyarrow as pa

    from pipeline.ingest import fno

    d1, d2, d3 = date(2015, 3, 5), date(2015, 3, 6), date(2015, 3, 9)
    near, far, never = date(2015, 3, 26), date(2015, 12, 24), date(2019, 6, 27)

    def row(d, expiry, lot):
        return {"symbol": "NIFTY", "date": d, "instrument": "OPT", "expiry": expiry, "strike": 9000.0,
                "option_type": "CE", "close": 1.0, "contracts": 0, "oi": 0, "chg_oi": 0,
                "turnover": 0.0, "lot_size": lot}

    table = pa.Table.from_pylist([
        row(d1, near, 25), row(d2, near, None), row(d3, near, 25),
        row(d1, far, None), row(d2, far, 50), row(d3, far, None),
        row(d1, never, None),
    ], schema=fno.CONTRACT_SCHEMA)
    filled = {(r["date"], r["expiry"]): r["lot_size"] for r in fno.fill_untraded_lots(table).to_pylist()}
    assert filled[(d2, near)] == 25                 # from the same contract's earlier session
    assert filled[(d1, far)] == 50                  # nothing earlier: from its next traded session
    assert filled[(d3, far)] == 50
    assert filled[(d1, never)] == 25                # never traded: the session's most common lot


def test_legacy_two_digit_year_expiries_parse():
    """NSE's 2012-05-14 file writes expiries as 31-May-12; it failed the whole session."""
    lines = [
        LEGACY_HEADER,
        "FUTIDX,NIFTY,31-May-12,0,XX,4900,4950,4880,4920,4920,1000,2460.00,0,0,14-May-12,",
        "OPTIDX,NIFTY,28-Jun-2012,5000,CE,80,85,75,82,82,100,254.10,0,0,14-May-2012,",
    ]
    fut, opt = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2012, 5, 14), ["NIFTY"])
    assert fut["expiry"] == date(2012, 5, 31) and opt["expiry"] == date(2012, 6, 28)
    assert fut["lot_size"] == 50


def test_udiff_rows_use_published_lot_and_underlying():
    lines = [
        UDIFF_HEADER,
        "2026-09-11,2026-09-11,FO,NSE,IDO,57153,,NIFTY,,2026-09-22,2026-09-22,24000.00,CE,NIFTY2692224000CE,"
        "19.70,28.90,12.60,22.20,23.40,22.90,23398.10,22.20,3257735,1007110,161659,252389471470.50,69369,F1,65,,,,,",
        "2026-09-11,2026-09-11,FO,NSE,IDF,35001,,NIFTY,,2026-09-29,2026-09-29,,,NIFTY26SEPFUT,"
        "23500,23520,23400,23485.20,23480,23484,23398.10,23485.20,17933110,-396630,50530,76849454578.00,1,F1,65,,,,,",
        "2026-09-11,2026-09-11,FO,NSE,STO,99,,RELIANCE,,2026-09-29,2026-09-29,1400.00,CE,X,"
        "1,1,1,1,1,1,1,1,1,1,1,1,1,F1,500,,,,,",
    ]
    opt, fut = nse.parse_fo_bhavcopy(_zip("\n".join(lines)), date(2026, 9, 11), ["NIFTY"])
    assert opt["instrument"] == "OPT" and opt["strike"] == 24000 and opt["option_type"] == "CE"
    assert opt["contracts"] == 161659 and opt["oi"] == 3257735 and opt["lot_size"] == 65
    assert opt["underlying"] == 23398.10 and opt["prev_close"] == 22.90 and opt["trades"] == 69369
    assert fut["instrument"] == "FUT" and fut["strike"] is None and fut["option_type"] is None
    assert fut["turnover"] / (fut["close"] * 65) == pytest.approx(fut["contracts"], rel=0.005)


def test_market_activity_report_fills_a_session_with_no_bhavcopy():
    """2013-10-09 has no F&O bhavcopy; its Market Activity zip does. Rows are real, padding included."""
    fo = ("INSTRUMENT,SYMBOL    ,EXP_DATE  ,OPEN_PRICE ,HI_PRICE   ,LO_PRICE   ,CLOSE_PRICE,OPEN_INT*      ,"
          "TRD_VAL           ,TRD_QTY          ,NO_OF_CONT       ,NO_OF_TRADE\n"
          "FUTIDX    ,BANKNIFTY ,26/12/2013,00010156.85,00010555.50,00010150.00,00010544.90,000000000005100,"
          "       43972580.00,             4225,              169,              131\n"
          "FUTIDX    ,NIFTY     ,26/12/2013,00006004.00,00006138.00,00005988.00,00006129.50,000000000248800,"
          "     1056717815.00,           173900,             3478,             2911\n"
          "FUTSTK    ,BHEL      ,26/12/2013,00000143.80,00000146.30,00000143.80,00000146.20,000000000022000,"
          "        1160200.00,             8000,              004,              004\n")
    op = ("INSTRUMENT,SYMBOL    ,EXP_DATE  ,STR_PRICE  ,OPT_TYPE,OPEN_PRICE ,HI_PRICE   ,LO_PRICE   ,CLOSE_PRICE,"
          "OPEN_INT*      ,TRD_QTY          ,NO_OF_CONT       ,NO_OF_TRADE,NOTION_VAL,PR_VAL\n"
          "OPTIDX    ,NIFTY     ,26/12/2013,00005700.00,CE      ,00000465.00,00000535.00,00000465.00,00000535.00,"
          "000000000083900,              800,              016,008,4966792.50,406792.50\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("fo09102013.csv", fo)
        zf.writestr("op09102013.csv", op)
        zf.writestr("fohelp.txt", "help")
    fut, opt = nse.parse_fo_market_activity(buf.getvalue(), date(2013, 10, 9), ["NIFTY"])

    assert fut["instrument"] == "FUT" and fut["expiry"] == date(2013, 12, 26) and fut["strike"] is None
    assert fut["close"] == 6129.50 and fut["oi"] == 248800 and fut["contracts"] == 3478
    assert fut["lot_size"] == 50 and fut["turnover"] == 1056717815.00
    assert opt["instrument"] == "OPT" and opt["strike"] == 5700 and opt["option_type"] == "CE"
    assert opt["close"] == opt["settle"] == 535.0 and opt["oi"] == 83900 and opt["chg_oi"] is None
    assert opt["contracts"] == 16 and opt["lot_size"] == 50 and opt["turnover"] == 4966792.50 and opt["trades"] == 8


def test_fovolt_yields_prices_only_from_the_2011_layout():
    new = (b"Date, Symbol, Underlying Close Price (A), Underlying Previous Day Close Price (B), x, x, x, "
           b"Underlying Annualised Volatility (F), Futures Close Price (G), x, x, x, x, x, x, x\n"
           b"14-Jun-11,NIFTY,5500.5,5482.8,  0.0032,  0.0088,  0.0086,  0.1641,5515.8,5498.5,"
           b"  0.0031,  0.0091,  0.0089,  0.1694,  0.0089,  0.1694\n"
           b"14-Jun-11,RELIANCE,900,890,0,0,0,0.3,901,891,0,0,0,0.3,0,0.3\n")
    (row,) = nse.parse_fovolt(new, date(2011, 6, 14), ["NIFTY"])
    assert row["close"] == 5500.5 and row["prev_close"] == 5482.8
    assert row["underlying_vol"] == 0.1641 and row["futures_close"] == 5515.8 and row["source"] == "fovolt"

    old = (b"DATE ,SYMBOL,UNDERLYING DAILY VOLATILITY,UNDERLYING ANNUALISED VOLATILITY, FUTURES VOLATILITY,"
           b"FUTURES ANNUALISED VOLATILITY ,APPLICABLE VOLATILITY,APPLICABLE ANNUALISED VOLATILITY\n"
           b"03-Jan-11,NIFTY,.91795,17.537407,.979792,18.718896,.979792,18.718896\n")
    assert nse.parse_fovolt(old, date(2011, 1, 3), ["NIFTY"]) == []


def test_near_future_proxy_skips_expired_contracts():
    d = date(2011, 1, 28)
    rows = [
        {"symbol": "NIFTY", "instrument": "FUT", "expiry": date(2011, 1, 27), "close": 5500.0},
        {"symbol": "NIFTY", "instrument": "FUT", "expiry": date(2011, 2, 24), "close": 5520.0},
        {"symbol": "NIFTY", "instrument": "FUT", "expiry": date(2011, 3, 31), "close": 5540.0},
    ]
    proxy = nse.near_future_close(rows, "NIFTY", d)
    assert proxy is not None and proxy["close"] == 5520.0 and proxy["source"] == "near_future"


def test_fno_bucket_never_defaults_to_the_terminal_bucket(monkeypatch):
    monkeypatch.setattr(type(config), "fno_bucket", "")
    with pytest.raises(RuntimeError, match="No F&O bucket"):
        config.require_fno_bucket()
    with pytest.raises(RuntimeError, match="terminal's own bucket"):
        config.require_fno_bucket(config.r2_bucket)
    assert config.require_fno_bucket("some-fno-bucket") == "some-fno-bucket"
