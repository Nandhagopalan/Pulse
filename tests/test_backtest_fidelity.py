"""
Hold the engine to the reference run.

These numbers came from the study that selected the strategy: full lake,
2008-01-16 to 2026-08-21, `balanced` preset, ₹50 lakh. They are checked in as
literals on purpose. If a refactor, an optimisation or a "harmless" tidy-up
moves any of them, the strategy being traded is no longer the one that was
validated, and this test is the only thing that would notice.

Skipped unless a local mirror of the lake is available, since CI has no R2
credentials:

    PULSE_BARS=/path/to/bars.parquet \
    PULSE_CONSTITUENTS=/path/to/constituents.parquet \
    uv run pytest tests/test_backtest_fidelity.py
"""
from __future__ import annotations

import os

import pytest

BARS = os.environ.get("PULSE_BARS")
CONSTITUENTS = os.environ.get("PULSE_CONSTITUENTS")

pytestmark = pytest.mark.skipif(
    not BARS, reason="set PULSE_BARS (and PULSE_CONSTITUENTS) to run the fidelity check"
)

# metric -> (expected, tolerance)
REFERENCE = {
    "cagr":         (0.1438, 0.0005),
    "max_dd":       (-0.1225, 0.0010),
    "sharpe":       (1.48, 0.02),
    "n_trades":     (972, 0),
    "win_rate":     (0.459, 0.005),
    "payoff":       (2.45, 0.03),
    "expectancy_r": (0.353, 0.006),
    "median_hold":  (19.0, 0.5),
    "exposure":     (0.344, 0.005),
    "end":          (6.08e7, 3.0e5),
}


@pytest.fixture(scope="module")
def summary():
    from pipeline.compute.strategy import backtest, data, rules
    from pipeline.compute.strategy.config import preset

    md = data.from_parquet(BARS, CONSTITUENTS)
    cfg = preset("balanced")
    feats = rules.compute_features(md, cfg)
    return backtest.summarise(backtest.run(md, feats, cfg, capital=5_000_000.0))


@pytest.mark.parametrize("metric", sorted(REFERENCE))
def test_matches_reference(summary, metric):
    want, tol = REFERENCE[metric]
    got = summary[metric]
    assert abs(got - want) <= tol, f"{metric}: got {got!r}, reference {want!r} (tol {tol})"


def test_no_liquidity_skips(summary):
    """
    The book grows from ₹50 lakh to ₹6.08 crore without ever being unable to
    fill for size — the evidence that this strategy scales to the target
    capital. If this starts failing, capacity has become the binding constraint.
    """
    assert summary["skipped"]["size"] == 0
