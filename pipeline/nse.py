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
from datetime import date, timedelta
from time import sleep
from typing import Dict, Iterator, List, Optional

import requests

from .config import config

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
        yield dict(zip(header, row))


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
