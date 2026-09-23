"""
A filing keyed to a symbol the tape never carried still finds its bars.

NSE re-keys a company's whole filing history to whatever symbol it trades under
today; the bars keep the symbol of the session they were printed in. The moment
a company is renamed, the two stop agreeing about every past action.

HEG is the case that surfaced it. HEG Limited became HEG Advanced Materials
(HEGAM) on 2026-09-22 with its ISIN unchanged, and its 2026-09-07 demerger — a
62% re-basing — arrived filed as HEGAM while the bars that fell said HEG.
Nothing joined, the audit reported a cliff with no action behind it, and the
nightly run failed on a finding that was fully explained in its own dataset.

Where the action states a ratio the same miss is silent and worse: it verifies
as `no_bars` and is never applied. Eighty-six filings across the lake are keyed
this way, MINDAIND's 2022 1:1 bonus among them.

What is pinned here is the rule, not the repair: a filing moves only when its
own symbol had no bars at the ex-date and exactly one other symbol sharing its
ISIN did. Ambiguity is left where the feed put it, because a wrong re-key
applies someone else's split to this company's history.
"""
from __future__ import annotations

from datetime import date

from pipeline.ingest.corporate_actions import resolve_symbol

# HEG's ISIN survived the rename; only the symbol moved.
ISIN = "INE545A01024"
SPANS = {
    ISIN: [
        ("HEG", date(2007, 1, 2), date(2026, 9, 21)),
        ("HEGAM", date(2026, 9, 22), date(2026, 9, 22)),
    ],
}
EX = date(2026, 9, 7)


def test_filing_moves_to_the_symbol_the_tape_used():
    """The demerger filed as HEGAM belongs to the bars that fell, which say HEG."""
    assert resolve_symbol("HEGAM", ISIN, EX, SPANS) == "HEG"


def test_a_filing_already_pointing_at_its_bars_is_left_alone():
    """HEG was trading on the ex-date, so nothing is second-guessed."""
    assert resolve_symbol("HEG", ISIN, EX, SPANS) == "HEG"


def test_after_the_rename_the_new_symbol_is_correct():
    """A filing dated once HEGAM is on the tape stays with HEGAM."""
    assert resolve_symbol("HEGAM", ISIN, date(2026, 9, 22), SPANS) == "HEGAM"


def test_ambiguity_is_never_guessed():
    """
    Two symbols carrying one ISIN at the ex-date is the case that must not move.

    Re-keying on a coin flip would apply one company's split to another's
    history, which is the failure this whole dataset exists to prevent — and it
    would do it silently, the way the 78-row accepted_residuals.json did.
    """
    spans = {ISIN: [
        ("HEG", date(2007, 1, 2), date(2026, 9, 21)),
        ("HEGAM", date(2026, 9, 1), date(2026, 9, 30)),
    ]}
    assert resolve_symbol("HEGAM", ISIN, EX, spans) == "HEGAM"


def test_an_unknown_company_is_left_alone():
    """No ISIN, or one the lake has never seen, changes nothing."""
    assert resolve_symbol("HEGAM", "", EX, SPANS) == "HEGAM"
    assert resolve_symbol("HEGAM", "INE000X01011", EX, SPANS) == "HEGAM"


def test_a_symbol_reused_by_another_company_is_not_captured():
    """
    Matching is on ISIN, never on the symbol alone.

    NSE reissues retired symbols, so a filing whose ISIN is absent from the
    lake must not be attracted to whoever holds that ticker now.
    """
    spans = {"INE999Z01011": [("HEG", date(2007, 1, 2), date(2026, 9, 21))]}
    assert resolve_symbol("HEGAM", ISIN, EX, spans) == "HEGAM"
