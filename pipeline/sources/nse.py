"""
NSE archive source: download and normalize official EOD files.

Two bhavcopy formats cover the history we care about, with entirely different
column names for the same data:

  legacy  < 2024-01-01  /content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
          SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,
          TIMESTAMP[,TOTALTRADES,ISIN]        — the last two appear only from ~2012
  UDiFF   >= 2024-01-01 /content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
          TradDt,...,ISIN,TckrSymb,SctySrs,...,OpnPric,HghPric,LwPric,ClsPric,
          LastPric,PrvsClsgPric,...,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,...

Both are mapped onto one row shape so everything downstream sees a single schema.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, timedelta
from time import sleep
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import requests

from ..config import config

ARCHIVES = "https://nsearchives.nseindia.com"

# NSE replaced the legacy bhavcopy with the CM-UDiFF format in 2024; the legacy
# archive covers everything before it.
UDIFF_FROM = date(2024, 1, 1)

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Cash-market series we track. Everything else in the file (government bonds,
# ETFs settled elsewhere, rights entitlements) is out of universe.
EQUITY_SERIES = {"EQ", "BE", "BZ"}

_session: Optional[requests.Session] = None


def http() -> requests.Session:
    """Shared session. NSE rejects non-browser user agents outright."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        _session = s
    return _session


def bhav_url(d: date) -> str:
    if d >= UDIFF_FROM:
        return f"{ARCHIVES}/content/cm/BhavCopy_NSE_CM_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip"
    mon = MONTHS[d.month - 1]
    return f"{ARCHIVES}/content/historical/EQUITIES/{d.year}/{mon}/cm{d.strftime('%d')}{mon}{d.year}bhav.csv.zip"


def index_close_url(d: date) -> str:
    return f"{ARCHIVES}/content/indices/ind_close_all_{d.strftime('%d%m%Y')}.csv"


def mto_url(d: date) -> str:
    return f"{ARCHIVES}/archives/equities/mto/MTO_{d.strftime('%d%m%Y')}.DAT"


def sec_bhavdata_url(d: date) -> str:
    """
    Full security-wise report: OHLC, volume *and* delivery, in one plain CSV.

    A separate publication from the bhavcopy zip covering the same session, which
    makes it a genuine second opinion on what we ingested — and its delivery
    column removes the need for the MTO file entirely.
    """
    return f"{ARCHIVES}/products/content/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"


def parse_sec_bhavdata(blob: bytes) -> Dict[str, dict]:
    """Parse sec_bhavdata_full into {symbol: {...}} for EQ/BE/BZ series."""
    out: Dict[str, dict] = {}
    for r in _rows(blob.decode("utf8", errors="replace")):
        # Header and values both carry padding spaces in this file.
        r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        series = (r.get("series") or "").upper()
        sym = r.get("symbol") or ""
        if not sym or series not in EQUITY_SERIES:
            continue
        out[sym] = {
            "prev_close": _num(r.get("prev_close")),
            "open": _num(r.get("open_price")),
            "high": _num(r.get("high_price")),
            "low": _num(r.get("low_price")),
            "close": _num(r.get("close_price")),
            "volume": _num(r.get("ttl_trd_qnty")),
            "delivery_pct": _num(r.get("deliv_per")),
        }
    return out


def fetch(url: str, retries: int = 4) -> Optional[bytes]:
    """
    GET with backoff. Returns None for 404 — on this source a 404 means
    "holiday or no such session", which is an expected outcome, not an error.

    Retries are spaced rather than immediate: over a run of several thousand
    files NSE will intermittently reset connections or throttle, and hammering
    it again in the same millisecond reliably fails the same way.
    """
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        if attempt:
            sleep(min(2 ** attempt, 15))
        try:
            r = http().get(url, timeout=config.nse_timeout)
            if r.status_code == 404:
                return None
            if r.status_code == 403:
                raise RuntimeError(
                    f"403 from NSE for {url} — the archive is refusing this IP. "
                    "If this is a CI runner, fetch from a residential IP instead."
                )
            r.raise_for_status()
            return r.content
        except Exception as err:  # noqa: BLE001 — retried, then surfaced
            last_err = err
    raise RuntimeError(f"failed after {retries} attempts: {url}") from last_err


WWW = "https://www.nseindia.com"
_www_ready = False


CA_PAGE = "/companies-listing/corporate-filings-actions"


def fetch_www_json(path: str, retries: int = 3, referer: str = CA_PAGE):
    """
    Call an api endpoint on the main NSE site.

    Unlike the archive host, www.nseindia.com refuses API calls that arrive
    without the cookies its landing pages set, so a session is bootstrapped by
    loading the HTML page that would normally issue the call. The referer has to
    match that page — the quote API rejects requests carrying the corporate
    filings referer. This host is also the one most likely to block datacenter
    IPs outright.
    """
    global _www_ready
    s = http()

    def bootstrap() -> None:
        global _www_ready
        s.get(WWW + referer, timeout=config.nse_timeout)
        _www_ready = True

    if not _www_ready:
        bootstrap()

    last_err: Optional[Exception] = None
    for attempt in range(retries):
        if attempt:
            sleep(2 ** attempt)
        try:
            r = s.get(WWW + path, headers={"Referer": WWW + referer}, timeout=config.nse_timeout)
            if r.status_code in (401, 403, 404):
                bootstrap()
                raise RuntimeError(f"{r.status_code} from {path}")
            r.raise_for_status()
            return r.json()
        except Exception as err:  # noqa: BLE001 — retried, then surfaced
            last_err = err
    raise RuntimeError(f"NSE api failed after {retries} attempts: {path}") from last_err


def corporate_actions_url(from_d: date, to_d: date) -> str:
    return (
        "/api/corporates-corporateActions?index=equities"
        f"&from_date={from_d.strftime('%d-%m-%Y')}&to_date={to_d.strftime('%d-%m-%Y')}"
    )


def _num(v: Optional[str]) -> float:
    try:
        return float((v or "").strip())
    except ValueError:
        return 0.0


def _csv_from_zip(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if name is None:
            raise ValueError("bhavcopy zip contained no csv")
        return zf.read(name).decode("utf8", errors="replace")


def _rows(text: str) -> Iterator[Dict[str, str]]:
    """CSV rows keyed by lower-cased, stripped header names."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return
    for row in reader:
        if not row or not any(c.strip() for c in row):
            continue
        # strict=False on purpose: NSE has shipped rows with a trailing empty
        # field, and one ragged row must not sink the night's ingest.
        yield dict(zip(header, row, strict=False))


def parse_bhavcopy(blob: bytes, d: date) -> List[dict]:
    """Normalize one session's bhavcopy into equity bars."""
    udiff = d >= UDIFF_FROM
    out: List[dict] = []
    for r in _rows(_csv_from_zip(blob)):
        if udiff:
            sym, series, isin = r.get("tckrsymb"), r.get("sctysrs"), r.get("isin")
            o, h, lo, c = r.get("opnpric"), r.get("hghpric"), r.get("lwpric"), r.get("clspric")
            prev, vol, val, trades = r.get("prvsclsgpric"), r.get("ttltradgvol"), r.get("ttltrfval"), r.get("ttlnboftxsexctd")
        else:
            sym, series, isin = r.get("symbol"), r.get("series"), r.get("isin")
            o, h, lo, c = r.get("open"), r.get("high"), r.get("low"), r.get("close")
            prev, vol, val, trades = r.get("prevclose"), r.get("tottrdqty"), r.get("tottrdval"), r.get("totaltrades")

        sym = (sym or "").strip()
        series = (series or "").strip().upper()
        if not sym or series not in EQUITY_SERIES:
            continue
        close = _num(c)
        if close <= 0:
            continue  # halted/no-trade row — carrying the last bar forward is the caller's job

        out.append({
            "symbol": sym,
            "date": d,
            "series": series,
            "isin": (isin or "").strip() or None,
            "open": _num(o),
            "high": _num(h),
            "low": _num(lo),
            "close": close,
            "prev_close": _num(prev),
            "volume": int(_num(vol)),
            "traded_value": _num(val),
            "trades": int(_num(trades)),
        })
    return out


def parse_index_close(blob: bytes, d: date) -> List[dict]:
    """Normalize the all-indices close file. Available from ~2013 onward only."""
    out: List[dict] = []
    for r in _rows(blob.decode("utf8", errors="replace")):
        name = (r.get("index name") or "").strip().upper()
        close = _num(r.get("closing index value"))
        if not name or close <= 0:
            continue
        out.append({
            "index_name": name,
            "date": d,
            "open": _num(r.get("open index value")),
            "high": _num(r.get("high index value")),
            "low": _num(r.get("low index value")),
            "close": close,
        })
    return out


def parse_delivery(blob: bytes) -> Dict[str, float]:
    """
    Security-wise delivery percentages from the MTO file.

    Fixed-position records: type 20 rows are
    <20, srno, symbol, series, traded_qty, deliverable_qty, pct>.
    Only recent sessions are archived, so this is latest-session context only —
    never part of the historical lake.
    """
    out: Dict[str, float] = {}
    for line in blob.decode("utf8", errors="replace").splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 7 or f[0] != "20" or f[3] not in EQUITY_SERIES:
            continue
        out[f[2]] = _num(f[6])
    return out


def sessions(start: date, end: date) -> Iterator[date]:
    """Candidate trading days, newest first. Weekends are skipped; holidays are
    discovered by the archive returning 404, which needs no holiday calendar."""
    d = end
    while d >= start:
        if d.weekday() < 5:
            yield d
        d -= timedelta(days=1)


# ── Equity derivatives: the F&O bhavcopy ─────────────────────────────────────
#
# Same archive host, same zip-of-CSV shape and the same UDiFF cut-over date as the
# cash bhavcopy, with different column names on each side of it:
#
#   legacy  < 2024-01-01  /content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip
#           INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,
#           CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP
#   UDiFF   >= 2024-01-01 /content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
#           FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,OpnPric..ClsPric,PrvsClsgPric,
#           UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,
#           TtlNbOfTxsExctd,NewBrdLotQty (among others)
#
# Two unit facts hold in *both* layouts and are easy to get wrong:
#   - volume is in contracts, but open interest is in units (contracts x lot).
#     Futures turnover / (close x lot) reproduces the volume field, and summed
#     index-option OI / the clearing house's participant-wise contract count
#     comes out at the lot size.
#   - turnover is notional, (strike + premium) x units for an option.
# The legacy file has no lot size column; `lot_from_turnover` recovers it.
#
# Legacy SETTLE_PR on option rows is not reliable as the option's own settlement
# (in 2023 files it carries the underlying's close), so option prices come from
# CLOSE, which holds the settlement value even for strikes that did not trade.

INDEX_FO_KINDS = {"FUTIDX": "FUT", "OPTIDX": "OPT", "IDF": "FUT", "IDO": "OPT"}


def fo_bhav_url(d: date) -> str:
    if d >= UDIFF_FROM:
        return f"{ARCHIVES}/content/fo/BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip"
    mon = MONTHS[d.month - 1]
    return (f"{ARCHIVES}/content/historical/DERIVATIVES/{d.year}/{mon}/"
            f"fo{d.strftime('%d')}{mon}{d.year}bhav.csv.zip")


def fovolt_url(d: date) -> str:
    """Clearing-house daily volatility file. From April 2011 it also carries the underlying's close."""
    return f"{ARCHIVES}/archives/nsccl/volt/FOVOLT_{d.strftime('%d%m%Y')}.csv"


def _int(v: Optional[str]) -> int:
    return round(_num(v))


def _opt_num(v: Optional[str]) -> Optional[float]:
    text = (v or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _notional_price(row: dict) -> Optional[float]:
    """The price turnover is struck on: the futures price, or strike + premium for an option."""
    price = row["close"] if row["instrument"] == "FUT" else (row["strike"] or 0.0) + row["close"]
    return price if price > 0 else None


def lot_from_turnover(row: dict) -> Optional[int]:
    """
    One legacy row's lot size, from its notional turnover: turnover / (price x contracts).

    Turnover is struck at traded prices across the session, not at the close, so a
    single row is only approximately right. `_fill_legacy_lots` does not trust any
    one row; this is the per-row building block and a diagnostic.
    """
    price = _notional_price(row)
    if price is None or row["contracts"] <= 0 or row["turnover"] <= 0:
        return None
    lot = round(row["turnover"] / (price * row["contracts"]))
    return lot if lot > 0 else None


# The smallest lot revision in NSE's published history is 75 -> 65 (13%). An
# estimate within LOT_SNAP of a lot the session has already established is noise
# to round away, never a revision being hidden.
LOT_SNAP = 0.10

# Traded options an expiry needs before its median may establish a lot that the
# session's thinner expiries snap to.
LOT_CONFIDENT_ROWS = 10


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _fill_legacy_lots(rows: List[dict]) -> None:
    """
    Lot size per (symbol, expiry) for legacy files, which do not publish it.

    Each traded row implies a lot, turnover / (price x contracts). Three choices
    make that usable:

    - **Options, not futures.** An option's notional price is mostly strike, so a
      volatile session barely moves it. A future's is all price: on 2022-02-24,
      with NIFTY down ~5%, the most traded future implied 50.78 and rounded to 51.
    - **The median per expiry**, because a thin far-dated contract can print a
      close far from where it traded (one 2022 weekly's 18100 PE implied 50.9).
    - **Per expiry, not per session.** NSE revises lots at expiry boundaries and
      contracts listed earlier keep their size. From Nov 2014 to Oct 2015 NIFTY's
      near months traded in lots of 25 while its long-dated options stayed at 50,
      and open interest agrees: all multiples of 50 on the long-dated contracts,
      barely half on the near ones.

    An estimate snaps to the nearest lot that a well-traded expiry established
    the same session, when within LOT_SNAP, and otherwise rounds. Scored against
    the lot NSE publishes in 2025-26 UDiFF files, 7,191 expiry-sessions, it
    matches every one. An expiry with no trade that session is left None:
    `ingest.fno.fill_untraded_lots` carries the contract's own lot to it.
    """
    opt: Dict[Tuple[str, date], List[float]] = {}
    fut: Dict[Tuple[str, date], List[float]] = {}
    for r in rows:
        price = _notional_price(r)
        if price is None or r["contracts"] <= 0 or r["turnover"] <= 0:
            continue
        target = opt if r["instrument"] == "OPT" else fut
        target.setdefault((r["symbol"], r["expiry"]), []).append(r["turnover"] / (price * r["contracts"]))

    estimates: Dict[Tuple[str, date], float] = {}
    established: Dict[str, List[int]] = {}
    for key in set(opt) | set(fut):
        options = opt.get(key, [])
        est = _median(options or fut[key])
        estimates[key] = est
        if len(options) >= LOT_CONFIDENT_ROWS and round(est) > 0:
            established.setdefault(key[0], []).append(round(est))

    lots: Dict[Tuple[str, date], int] = {}
    for key, est in estimates.items():
        known = established.get(key[0], [])
        nearest: Optional[int] = min(known, key=lambda lot: abs(est - lot)) if known else None
        if nearest is not None and abs(est - nearest) / nearest <= LOT_SNAP:
            lots[key] = nearest
        elif round(est) > 0:
            lots[key] = round(est)

    for r in rows:
        r["lot_size"] = lots.get((r["symbol"], r["expiry"]))


def _legacy_date(text: Optional[str]) -> date:
    """Legacy F&O dates are DD-Mon-YYYY, except some 2012 files that write DD-Mon-YY (2012-05-14)."""
    value = (text or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised legacy date {value!r}")


def parse_fo_bhavcopy(blob: bytes, d: date, symbols: Iterable[str]) -> List[dict]:
    """Index futures and options for `symbols` from one session's F&O bhavcopy, one row shape for both layouts."""
    wanted = {s.upper() for s in symbols}
    udiff = d >= UDIFF_FROM
    out: List[dict] = []
    for r in _rows(_csv_from_zip(blob)):
        if udiff:
            kind = INDEX_FO_KINDS.get((r.get("fininstrmtp") or "").strip().upper())
            sym = (r.get("tckrsymb") or "").strip().upper()
        else:
            kind = INDEX_FO_KINDS.get((r.get("instrument") or "").strip().upper())
            sym = (r.get("symbol") or "").strip().upper()
        if kind is None or sym not in wanted:
            continue

        row: dict
        if udiff:
            expiry = date.fromisoformat((r.get("xprydt") or "").strip())
            strike = _num(r.get("strkpric"))
            otype = (r.get("optntp") or "").strip().upper()
            row = {
                "open": _num(r.get("opnpric")), "high": _num(r.get("hghpric")),
                "low": _num(r.get("lwpric")), "close": _num(r.get("clspric")),
                "settle": _num(r.get("sttlmpric")), "prev_close": _opt_num(r.get("prvsclsgpric")),
                "underlying": _opt_num(r.get("undrlygpric")),
                "contracts": _int(r.get("ttltradgvol")), "oi": _int(r.get("opnintrst")),
                "chg_oi": _int(r.get("chnginopnintrst")), "turnover": _num(r.get("ttltrfval")),
                "trades": _int(r.get("ttlnboftxsexctd")), "lot_size": _int(r.get("newbrdlotqty")) or None,
            }
        else:
            expiry = _legacy_date(r.get("expiry_dt"))
            strike = _num(r.get("strike_pr"))
            otype = (r.get("option_typ") or "").strip().upper()
            row = {
                "open": _num(r.get("open")), "high": _num(r.get("high")),
                "low": _num(r.get("low")), "close": _num(r.get("close")),
                "settle": _num(r.get("settle_pr")), "prev_close": None, "underlying": None,
                "contracts": _int(r.get("contracts")), "oi": _int(r.get("open_int")),
                "chg_oi": _int(r.get("chg_in_oi")), "turnover": _num(r.get("val_inlakh")) * 1e5,
                "trades": None, "lot_size": None,
            }

        is_option = kind == "OPT"
        if is_option and otype not in ("CE", "PE"):
            continue
        row.update(symbol=sym, date=d, instrument=kind, expiry=expiry,
                   strike=strike if is_option else None,
                   option_type=otype if is_option else None)
        out.append(row)

    if not udiff:
        _fill_legacy_lots(out)
    return out


def fo_market_activity_url(d: date) -> str:
    """
    The F&O Market Activity Report, a second publication of the session's contracts.

    NSE has no F&O bhavcopy at all for a few sessions that did trade, 2013-10-09
    and 2021-03-30 among them, but it does publish this zip for them.
    """
    return f"{ARCHIVES}/archives/fo/mkt/fo{d.strftime('%d%m%Y')}.zip"


def parse_fo_market_activity(blob: bytes, d: date, symbols: Iterable[str]) -> List[dict]:
    """
    Index futures and options from the Market Activity zip, in the bhavcopy row shape.

    Contracts come from its foDDMMYYYY.csv (futures) and opDDMMYYYY.csv (options).
    It lists traded contracts only, so a session parsed from here has no untraded
    strikes. In exchange it carries traded quantity beside contracts, which gives
    the lot size exactly. On 2013-10-08, when both publications exist, all 199
    traded NIFTY options agree with the bhavcopy on close and open interest.

    Numbers are zero-padded ("00005700.00"), dates are DD/MM/YYYY, and there is no
    settlement price or change in open interest: `settle` takes the close and
    `chg_oi` is None.
    """
    wanted = {s.upper() for s in symbols}
    stamp = d.strftime("%d%m%Y")
    out: List[dict] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = {name.lower(): name for name in zf.namelist()}
        for member, kind, instrument in ((f"fo{stamp}.csv", "FUT", "FUTIDX"), (f"op{stamp}.csv", "OPT", "OPTIDX")):
            if member not in members:
                continue
            for raw in _rows(zf.read(members[member]).decode("utf8", errors="replace")):
                r = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
                sym = (r.get("symbol") or "").upper()
                if (r.get("instrument") or "").upper() != instrument or sym not in wanted:
                    continue
                otype = (r.get("opt_type") or "").upper() if kind == "OPT" else None
                if kind == "OPT" and otype not in ("CE", "PE"):
                    continue
                contracts, qty = _int(r.get("no_of_cont")), _int(r.get("trd_qty"))
                close = _num(r.get("close_price"))
                out.append({
                    "symbol": sym, "date": d, "instrument": kind,
                    "expiry": datetime.strptime(r.get("exp_date") or "", "%d/%m/%Y").date(),
                    "strike": _num(r.get("str_price")) if kind == "OPT" else None,
                    "option_type": otype,
                    "open": _num(r.get("open_price")), "high": _num(r.get("hi_price")),
                    "low": _num(r.get("lo_price")), "close": close, "settle": close,
                    "prev_close": None, "underlying": None,
                    "contracts": contracts, "oi": _int(r.get("open_int*")), "chg_oi": None,
                    "turnover": _num(r.get("notion_val") if kind == "OPT" else r.get("trd_val")),
                    "trades": _int(r.get("no_of_trade")),
                    "lot_size": qty // contracts if contracts and qty % contracts == 0 else None,
                })
    return out


def parse_fovolt(blob: bytes, d: date, symbols: Iterable[str]) -> List[dict]:
    """
    Underlying closes from the clearing-house volatility file.

    Fields are positional because the header wording has drifted over the years:
    0 date, 1 symbol, 2 underlying close, 3 previous close, 7 underlying
    annualised volatility, 8 futures close. Files before April 2011 have eight
    fields and no prices at all; they yield nothing.
    """
    wanted = {s.upper() for s in symbols}
    out: List[dict] = []
    reader = csv.reader(io.StringIO(blob.decode("utf8", errors="replace")))
    next(reader, None)
    for fields in reader:
        f = [x.strip() for x in fields]
        if len(f) < 9 or f[1].upper() not in wanted:
            continue
        close = _num(f[2])
        if close <= 0:
            continue
        out.append({
            "symbol": f[1].upper(), "date": d, "close": close,
            "prev_close": _opt_num(f[3]), "underlying_vol": _opt_num(f[7]),
            "futures_close": _opt_num(f[8]), "source": "fovolt",
        })
    return out


def near_future_close(rows: List[dict], symbol: str, d: date) -> Optional[dict]:
    """Spot stand-in when FOVOLT has no price: the nearest unexpired future's close."""
    futs = [r for r in rows if r["symbol"] == symbol and r["instrument"] == "FUT"
            and r["expiry"] >= d and r["close"] > 0]
    if not futs:
        return None
    near = min(futs, key=lambda r: r["expiry"])
    return {"symbol": symbol, "date": d, "close": near["close"], "prev_close": None,
            "underlying_vol": None, "futures_close": near["close"], "source": "near_future"}

