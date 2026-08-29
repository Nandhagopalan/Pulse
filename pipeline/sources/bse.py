"""
BSE access, for the one thing NSE will not serve to a script: industry labels.

NSE publishes a four-level classification on every quote page, but the endpoint
behind it (`/api/quote-equity`) is refused at Akamai's edge with a 403 for
anything that is not a browser. Its downloadable index files carry `Industry`,
yet they only ever describe *current* index members — 750 names at the very
most, none of them delisted.

BSE exposes the same classification through an API that answers plainly, and its
scrip master retains delisted and suspended companies. Joining on ISIN rather
than ticker sidesteps the two exchanges' differing symbols, and matters most for
the companies that no longer trade under any symbol at all.

Coverage is 81% of the lake's equities — and, the reason this source was chosen,
81% of the delisted ones too. A label set that covered only survivors would turn
any filter built on it into a survivorship screen, which is worse than having no
labels.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import requests

from ..config import config

API = "https://api.bseindia.com/BseIndiaAPI/api"
WWW = "https://www.bseindia.com"

# Every status the scrip master will return. "Active" alone would rebuild the
# survivorship problem this module exists to avoid.
STATUSES = ("Active", "Delisted", "Suspended")

_session: Optional[requests.Session] = None


def http() -> requests.Session:
    """Shared session. The API checks Origin and Referer, not just the agent."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{WWW}/corporates/List_Scrips.html",
            "Origin": WWW,
        })
        _session = s
    return _session


def _get(path: str, retries: int = 3):
    last: Optional[Exception] = None
    for attempt in range(retries):
        if attempt:
            from time import sleep
            sleep(2 ** attempt)
        try:
            r = http().get(f"{API}{path}", timeout=config.nse_timeout)
            r.raise_for_status()
            return r.json()
        except Exception as err:  # noqa: BLE001 — retried, then surfaced
            last = err
    raise RuntimeError(f"BSE api failed after {retries} attempts: {path}") from last


def scrip_master() -> List[dict]:
    """
    Every equity scrip BSE knows about, across all three statuses.

    Returns the raw rows; `ISIN_NUMBER` is the join key and `SCRIP_CD` is what
    the per-scrip header call needs. The `industry` query parameter this
    endpoint accepts is ignored server-side — it returns the whole list whatever
    is passed — so the classification has to come from `header()`, one scrip at
    a time.
    """
    out: List[dict] = []
    for status in STATUSES:
        rows = _get(
            "/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity"
            f"&status={status}"
        )
        for r in rows:
            r["Status"] = status
            out.append(r)
        print(f"[industry] BSE scrip master, {status}: {len(rows)} rows")
    return out


def header(scrip_code: str) -> Dict[str, Optional[str]]:
    """
    Industry and macro sector for one scrip.

    `Industry` is the basic industry a person would recognise ("Auto Components
    & Equipments"); `IndustryNew` is the macro sector above it ("Automobile and
    Auto Components"), and is the level coarse enough to rank sectors against
    each other.
    """
    d = _get(f"/ComHeadernew/w?quotetype=EQ&scripcode={scrip_code}&seriesid=")
    return {
        "isin": (d.get("ISIN") or "").strip() or None,
        "industry": (d.get("Industry") or "").strip() or None,
        "macro": (d.get("IndustryNew") or "").strip() or None,
    }
