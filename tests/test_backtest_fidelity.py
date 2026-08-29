"""
Hold the engine to the reference run.

These numbers came from the study that selected the strategy the book actually
trades: full lake, `deployed` preset, ₹50 lakh. They are checked in as literals
on purpose. If a refactor, an optimisation or a "harmless" tidy-up moves any of
them, the strategy being traded is no longer the one that was validated, and
this test is the only thing that would notice.

Two things changed here after the previous baseline went stale, both worth
keeping:

  * **The window is pinned.** The old reference was taken over a lake ending
    2026-08-21 and then compared against whatever the mirror held later, so
    every new session drifted `cagr` and `end` while the trade population stayed
    put — the test failed for growing, not for breaking. `END` fixes the last
    session so the reference means one thing forever.
  * **It follows the traded preset.** It pinned `balanced`, which is no longer
    what the book runs, and whose figures were measured before the sector-label
    correction — they included the look-ahead that correction removed. Pinning a
    strategy nobody trades to numbers nobody trusts is worse than no test.

Skipped unless a local mirror of the lake is available, since CI has no R2
credentials:

    PULSE_BARS=/path/to/bars.parquet uv run pytest tests/test_backtest_fidelity.py
"""
from __future__ import annotations

import os

import pytest

BARS = os.environ.get("PULSE_BARS")
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")

pytestmark = pytest.mark.skipif(
    not BARS, reason="set PULSE_BARS (and PULSE_CONSTITUENTS) to run the fidelity check"
)

# Exclusive upper bound: the last session in the reference run is 2026-08-28.
# A lake that has grown past it must not move these numbers.
END = "2026-08-29"

# metric -> (expected, tolerance)
REFERENCE = {
    "cagr":         (0.1698, 0.0005),
    "max_dd":       (-0.2468, 0.0010),
    "sharpe":       (1.17, 0.02),
    "n_trades":     (1080, 0),
    "win_rate":     (0.4222, 0.005),
    "payoff":       (2.921, 0.03),
    "expectancy_r": (0.3948, 0.006),
    "median_hold":  (10.0, 0.5),
    "exposure":     (0.5195, 0.005),
    "end":          (9.27e7, 3.0e5),
}


@pytest.fixture(scope="module")
def summary():
    from pipeline.compute.strategy import backtest, data, rules
    from pipeline.compute.strategy.config import preset

    md = data.from_parquet(BARS, CONSTITUENTS)
    cfg = preset("deployed")
    feats = rules.compute_features(md, cfg)
    return backtest.summarise(
        backtest.run(md, feats, cfg, capital=5_000_000.0, end=END))


@pytest.mark.parametrize("metric", sorted(REFERENCE))
def test_matches_reference(summary, metric):
    want, tol = REFERENCE[metric]
    got = summary[metric]
    assert abs(got - want) <= tol, f"{metric}: got {got!r}, reference {want!r} (tol {tol})"


def test_no_liquidity_skips(summary):
    """
    No fill is ever prevented by the market being too thin.

    The book grows from ₹50 lakh to ₹9.27 crore without once asking for more
    than 5% of a name's 20-day turnover, which is the evidence that the strategy
    scales to the target capital. It matters more here than for the previous
    preset: this one averages ~52% invested against ~34%, so it asks the market
    for half again as much.

    Deliberately not asserted against every unsized entry. This book is designed
    to spend its cash, so it declines entries for want of money routinely — 57
    times over the record — and that is the strategy working, not a limit being
    hit. Only `liquidity` means capacity has started to bind.
    """
    assert summary["skipped"]["liquidity"] == 0
