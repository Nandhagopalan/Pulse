# scripts/

The research record behind [docs/strategy-engine.md](../docs/strategy-engine.md).

**Nothing here runs in production.** No workflow, no CI job and no pipeline
module imports this package — `pipeline/` never depends on `scripts/`, only the
other way round. These are the studies that selected the strategy the paper book
trades, kept so the findings in the design doc can be re-run rather than
believed. Section 1.2 of that doc cites `walkforward.py`, `overlay_recheck.py`
and `label_gap.py` by name.

They are studies, not tools: each prints a table and exits, and several of the
numbers they print are checked in as literals in
`tests/test_backtest_fidelity.py`.

## Running one

They read a **local Parquet mirror**, not R2, because a sweep re-reads the same
bars hundreds of times:

```bash
# Capture the mirror once (see pipeline/README.md), then:
PULSE_BARS=/path/bars.parquet \
PULSE_CONSTITUENTS=/path/constituents.parquet \
PULSE_INDUSTRY=/path/industry.parquet \
uv run python -m scripts.walkforward
```

| Variable | Needed by | What it points at |
| --- | --- | --- |
| `PULSE_BARS` | all | the daily bars, as one Parquet file |
| `PULSE_CONSTITUENTS` | all | `curated/instruments/constituents.parquet` |
| `PULSE_INDUSTRY` | the overlay studies | `curated/instruments/industry.parquet` |
| `PULSE_INDEX` | `benchmarks.py`, `ladder.py` | index bars, for the comparison series |
| `PULSE_RUNG` / `PULSE_RUNGS` | `rung.py`, `ladder.py` | which deployment rung to detail |
| `PULSE_COMMON` | `check_winners.py` | shared-position overlap input |
| `FNO_MIRROR` | `covered_call.py` | the local option-chain mirror its `extract` step writes from the F&O bucket |

## `walkforward.py` is the harness

Twenty of the twenty-one studies import `Engine`, `period`, `TRAIN_END`, `OOS1`
and `OOS2` from it. It defines the train/holdout split — parameters chosen on
data ending 2024-12-31, then applied untouched to 2025 and 2026 — and every other
script inherits that discipline rather than re-deciding it. **Do not change its
constants casually**: every number quoted in the design doc was produced against
them.

## Reading order

### 1. Out-of-sample validation

| Script | The question it answers |
| --- | --- |
| `walkforward.py` | Choose parameters on ≤ 2024, then look at 2025–26. The study that found the problem |
| `diagnose_years.py` | Why 2022 and 2024 were flat — looking for a cause before trying to fix two named years, which is how a backtest gets curve-fitted |
| `whipsaw.py` | Does damping the regime's flipping help, and does it survive the holdout? |
| `dd_reality.py` | How much bigger is the real drawdown than the backtest promised? (−9.32% became −14.14%) |

### 2. The sector overlay — the finding that changed the strategy

The overlay read labels that existed only for today's NIFTY 500: 500 of 3,967
equities. It silently encoded "hold only companies in the NIFTY 500 in 2026".
This chain is how that was found, doubted, re-tested on honest labels, and
resolved. Read it in order — each script exists because the previous one's result
was too good to accept.

| Script | The question it answers |
| --- | --- |
| `nosector_sweep.py` | Re-tune on the uncontaminated base — every parameter had been chosen *around* the overlay |
| `verify_nosector.py` | Is the no-overlay result tradeable, or an artifact? (+23.75% from *removing* a filter deserves suspicion before capital) |
| `check_winners.py` | Are the big holdout winners real, or corporate-action artifacts? |
| `final_study.py` | The overlay, the ETFs, and what is actually left — six silver ETFs bought the same morning were one bet wearing six position slots |
| `label_gap.py` | Does the missing 19% of sector labels actually matter? |
| `overlay_recheck.py` | Does the overlay earn its place once the labels are honest? |
| `rerun_strategy.py` | The rebuild: structure chosen on 2008–2024 alone, then applied untouched to 2025–26 |

### 3. Sizing, deployment and the drawdown budget

| Script | The question it answers |
| --- | --- |
| `deployment_study.py` | Is holding ~65% cash an edge or a drag? Was 2025 weak from selection, or from too little capital working? |
| `fit_budget.py` | The best book that still fits inside a −15% drawdown |
| `experiments.py` | Six changes, one axis at a time, then combined — each against the same budget |
| `aggressive.py` | The book rebuilt for a trader who will not park the float and will wear 20% |
| `rollback.py` | How fast the book can get out, and what that buys at full deployment |
| `robust.py` | Which part of the aggressive frontier is real and which is selection noise |
| `ladder.py` | The deployment ladder, at the one regime speed that survived every test |
| `rung.py` | Everything about one rung of the ladder, in the form the year table takes |
| `holdout3y.py` | A three-year holdout: train to 2023, then face 2024, 2025 and 2026 cold |
| `benchmarks.py` | The book against the indices a swing trader would otherwise have bought |
| `entry_pacing.py` | Why a sideways year was flat, and what capping fills per session does — the evidence for `max_new_per_session` |

### 4. Options

A separate study with its own data, so it does not use the `walkforward.py`
harness. It reads NIFTY monthly options from the F&O bucket and splits time its
own way: calibrate on 2011–2022, test on 2024–2026. Its findings are in
[docs/covered-call-backtest.md](../docs/covered-call-backtest.md).

| Script | The question it answers |
| --- | --- |
| `covered_call.py` | Which covered call on NIFTYBEES — strike, expiry, exit, adjustments, coverage, hedge — holds up out of sample, and is a butterfly the right hedge? (`extract`, `smoke`, `sweep`, `analyse`) |
| `swing_options.py` | Does carrying the swing book's F&O-eligible trades in stock calls or call spreads, instead of shares, improve it? No — [docs/swing-options-backtest.md](../docs/swing-options-backtest.md) (`extract`, `trades`, `smoke`, `sweep`, `analyse`) |

## Adding one

Import the harness rather than re-deriving the split, keep the docstring
answering *the question this run settles* — that is what makes the directory
readable two months later — and never let `pipeline/` import anything from here.
