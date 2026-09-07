"""
Pin the sector vocabulary to one label per sector.

The failure this guards against is silent: every consumer treats these labels
as opaque keys, so a sector that arrives under two spellings simply appears
twice, with its members, its breadth and its position cap split across the two.
"""
from __future__ import annotations

import pytest

from pipeline.ingest import taxonomy as tx


@pytest.mark.parametrize("raw, want", [
    # NSE serves the same sectors as BSE, without the commas.
    ("Oil Gas & Consumable Fuels", "Oil, Gas & Consumable Fuels"),
    ("Media Entertainment & Publication", "Media, Entertainment & Publication"),
    ("Oil, Gas & Consumable Fuels", "Oil, Gas & Consumable Fuels"),
    ("  Financial   Services ", "Financial Services"),
    (None, None),
    ("", None),
])
def test_spelling_variants_collapse(raw, want):
    assert tx.sector(raw) == want


def test_legacy_scheme_resolves_on_its_basic_industry():
    """
    NSE still answers the pre-2018 scheme for a few symbols. "CONSUMER GOODS"
    is not a variant spelling of anything current — NIC splits it — so it is
    placed by the basic industry underneath it, or not at all.
    """
    assert tx.sector("CONSUMER GOODS", "CONSUMER FOOD") == "Fast Moving Consumer Goods"
    assert tx.sector("CONSUMER GOODS", "Household Appliances") is None
    assert tx.sector("CONSUMER GOODS") is None


def test_unknown_sector_survives():
    """A sector added after this list was written is kept, not dropped."""
    assert tx.sector("Space & Defence") == "Space & Defence"


def test_reconcile_prefers_the_bse_spelling():
    rows = [
        {"source": "bse", "macro": "Oil, Gas & Consumable Fuels",
         "industry": "Gems, Jewellery And Watches"},
        {"source": "nse", "macro": "Oil Gas & Consumable Fuels",
         "industry": "Gems Jewellery And Watches"},
        {"source": "nse", "macro": "Oil Gas & Consumable Fuels",
         "industry": "Gems Jewellery And Watches"},
    ]
    tx.reconcile(rows)
    assert {r["macro"] for r in rows} == {"Oil, Gas & Consumable Fuels"}
    # Outnumbered two to one, and still the spelling that wins.
    assert {r["industry"] for r in rows} == {"Gems, Jewellery And Watches"}


def test_reconcile_repairs_a_mangled_dash():
    """
    One basic industry reaches us from NSE with its dash decoded into
    replacement characters. Where BSE has the same industry its spelling wins;
    where it does not, the run of replacement characters is still not a name.
    """
    rows = [{"source": "nse", "macro": "Capital Goods",
             "industry": "Dealers���Commercial Vehicles"}]
    tx.reconcile(rows)
    assert rows[0]["industry"] == "Dealers-Commercial Vehicles"


def test_reconcile_is_order_independent():
    """Two sources, no majority: the same table must reconcile the same way."""
    a = [{"source": "nse", "macro": None, "industry": "Tour Travel Related Services"},
         {"source": "nse", "macro": None, "industry": "Tour, Travel Related Services"}]
    b = list(reversed([dict(r) for r in a]))
    tx.reconcile(a)
    tx.reconcile(b)
    assert {r["industry"] for r in a} == {r["industry"] for r in b}


def test_canonical_sectors_are_already_canonical():
    """The pinned list must be a fixed point, or it disagrees with itself."""
    assert all(tx.sector(s) == s for s in tx.SECTORS)
    assert len({tx.key(s) for s in tx.SECTORS}) == len(tx.SECTORS)
