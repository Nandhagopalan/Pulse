"""
NSE's own industry classification, via the `nse` package.

NSE publishes four levels — macro, sector, industry, basic industry — on every
quote page. The endpoint the page itself calls, `/api/quote-equity`, is refused
at Akamai's edge with a 403 for anything that is not a browser, and stays refused
even with a correctly bootstrapped session. `getDetailedScripData` reaches the
same classification through a different endpoint that is not behind that filter,
which is the only reason this module exists rather than a scraper.

`sector` here is the same vocabulary as BSE's `IndustryNew`, and `basicIndustry`
the same as BSE's `Industry` — both exchanges publish the same NIC-derived
scheme. That is what lets `ingest/industry.py` merge the two without a mapping
layer, and why a symbol labelled by either source is labelled consistently.

Listed companies only. A delisted symbol resolves to nothing here, which is why
BSE remains the primary source: it is the one that still knows the dead.
"""
from __future__ import annotations

from typing import Optional

_client = None


def client(download_folder: str = "/tmp"):
    """
    Shared NSE client. Holds a cookie jar, so it is built once and reused.

    Imported lazily: the nightly bar ingest has no need of this dependency, and
    a missing optional package should not stop the pipeline from starting.
    """
    global _client
    if _client is None:
        from nse import NSE
        _client = NSE(download_folder=download_folder)
    return _client


def close() -> None:
    global _client
    if _client is not None:
        try:
            _client.exit()
        finally:
            _client = None


def classification(symbol: str) -> Optional[dict]:
    """
    macro / sector / industry / basic industry for one listed symbol.

    Returns None when the symbol resolves but carries no classification, which
    happens for recent listings and for instruments that are not companies.
    """
    data = client().getDetailedScripData(symbol)
    resp = (data.get("equityResponse") or [{}])[0]
    sec = resp.get("secInfo") or {}
    sector = (sec.get("sector") or "").strip() or None
    if not sector:
        return None
    return {
        "isin": (resp.get("metadata") or {}).get("isin") or None,
        "macro": sector,                                       # matches BSE IndustryNew
        "industry": (sec.get("basicIndustry") or "").strip() or None,
        "group": (sec.get("macro") or "").strip() or None,     # the level above sector
    }
