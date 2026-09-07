"""
One name per sector, whoever answered.

Both exchanges publish the same NIC-derived scheme, which is what lets
`ingest/industry.py` merge them without a mapping layer — but they do not
publish the same *strings* for it. NSE drops the commas, so its
"Oil Gas & Consumable Fuels" and BSE's "Oil, Gas & Consumable Fuels" are two
labels for one sector; the dash in one basic industry reaches us as three
replacement characters; and for a few symbols NSE still serves the pre-2018
scheme, in capitals, whose sector names have no equivalent under NIC.

Stored as fetched, that splits a sector in two everywhere it is read: the
sector view lists both halves with half the members each, breadth and the
sector score are computed over a fraction of the sector, and the strategy's
`max_per_sector` cap admits twice its intended weight because the two halves
count separately. Nothing downstream can detect it — every consumer treats
these labels as opaque keys — so the reconciliation belongs here, at the only
point where both vocabularies are visible at once.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# The macro sectors as BSE spells them. A closed set of 22, and the level the
# sector view and the overlay both rank on, so it is worth pinning by hand: a
# variant that reaches us in a spelling not listed here is a new sector or a
# new way of writing an old one, and both are worth noticing.
SECTORS: Tuple[str, ...] = (
    "Automobile and Auto Components",
    "Capital Goods",
    "Chemicals",
    "Construction",
    "Construction Materials",
    "Consumer Durables",
    "Consumer Services",
    "Diversified",
    "Fast Moving Consumer Goods",
    "Financial Services",
    "Forest Materials",
    "Healthcare",
    "Information Technology",
    "Media, Entertainment & Publication",
    "Metals & Mining",
    "Oil, Gas & Consumable Fuels",
    "Power",
    "Realty",
    "Services",
    "Telecommunication",
    "Textiles",
    "Utilities",
)

# NSE's pre-2018 sectors, which it still serves for a few symbols. They are not
# a punctuation variant of anything current — the old "CONSUMER GOODS" covers
# what NIC splits into fast-moving goods and durables — so they are resolved on
# the basic industry that arrives with them, keyed (sector, basic industry),
# rather than guessed from the sector name alone.
LEGACY: Dict[Tuple[str, str], str] = {
    ("consumergoods", "consumerfood"): "Fast Moving Consumer Goods",
}


def key(label: str) -> str:
    """
    Comparison form: letters and digits only, folded to lower case.

    Punctuation is exactly what the two exchanges disagree about, so it is what
    the comparison drops. It also collapses the replacement characters left by
    a mis-decoded dash onto the spelling that still has the dash.
    """
    return re.sub(r"[^a-z0-9]+", "", label.lower())


_BY_KEY = {key(s): s for s in SECTORS}


def clean(label: Optional[str]) -> Optional[str]:
    """Tidy a raw label: repair the mangled dash, collapse runs of whitespace."""
    if not label:
        return None
    out = re.sub("�+", "-", label)
    return re.sub(r"\s+", " ", out).strip() or None


def sector(label: Optional[str], industry: Optional[str] = None) -> Optional[str]:
    """
    The canonical name of a macro sector, or None if it cannot be placed.

    An unrecognised label that is not from the old scheme is returned tidied
    but otherwise untouched: it is more likely a sector added since this list
    was written than a mistake, and dropping it would lose the names under it.
    """
    label = clean(label)
    if not label:
        return None
    k = key(label)
    if k in _BY_KEY:
        return _BY_KEY[k]
    if label.isupper():  # the old scheme, and only it, shouts
        return LEGACY.get((k, key(industry or "")))
    return label


def reconcile(rows: List[dict]) -> List[dict]:
    """
    Rewrite `macro` and `industry` across the table so one label means one thing.

    Sectors resolve against `SECTORS`. Basic industries cannot: there are ~200
    of them and the set grows, so the spellings are reconciled against each
    other, with BSE's preferred where the two differ — BSE is the source that
    keeps the punctuation, and the one that also labels the delisted, so its
    spelling is the one most rows already carry.
    """
    variants: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for r in rows:
        ind = clean(r.get("industry"))
        if not ind:
            continue
        seen = variants.setdefault(key(ind), {})
        bse, n = seen.get(ind, (0, 0))
        seen[ind] = (bse or int(r.get("source") == "bse"), n + 1)
    # Ties broken on the spelling itself, so the same table always reconciles
    # to the same labels however the rows happen to be ordered.
    preferred = {k: max(v, key=lambda s: (v[s][0], v[s][1], s)) for k, v in variants.items()}

    unplaced: Dict[str, int] = {}
    for r in rows:
        ind = clean(r.get("industry"))
        r["industry"] = preferred.get(key(ind), ind) if ind else None
        raw = clean(r.get("macro"))
        r["macro"] = sector(raw, r["industry"])
        if raw and not r["macro"]:
            unplaced[raw] = unplaced.get(raw, 0) + 1
    for name, n in sorted(unplaced.items()):
        print(f"[industry] dropped {n} label(s) from a scheme with no NIC "
              f"equivalent: {name!r}")
    return rows
