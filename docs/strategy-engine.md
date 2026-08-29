# Pulse Strategy Engine — Design

**Status:** proposed, not yet built
**Owner:** this document is the reference the implementation is written against
**Companion:** [architecture.md](architecture.md) — the system this plugs into

A rules-based swing-trading engine that runs inside the nightly pipeline, keeps a
paper book, and tells you what to buy tomorrow morning and what to sell. It exists
so a strategy validated over 18.6 years of NSE history can be watched forward, in
the terminal, before any real money is committed.

The whole thing is built around one constraint: **every rule is a parameter, and
every parameter lives in one place.** Nothing about the strategy is hardcoded in a
job, a route or a component.

---

## 1. What was decided, and on what evidence

The strategy below was selected by backtesting over the full lake — 8,042,807
adjusted daily bars, 4,478 symbols, 2007-01-02 to 2026-08-21, including every
company later delisted, renamed or acquired.

| Measure | Result |
| --- | --- |
| CAGR | 14.38% |
| Maximum drawdown | −12.25% |
| Sharpe / Calmar | 1.48 / 1.17 |
| Trades | 972 (52/yr), 45.9% win rate, 2.45× payoff |
| Expectancy | +0.353R per trade |
| Median hold | 19 sessions (max 60) |
| Average deployment | 34.4% of capital |
| ₹50 lakh becomes | ₹6.08 crore |

> **Correction, August 2026.** These figures carry a look-ahead premium and
> should not be planned against. The sector overlay reads industry labels that
> existed for only 500 of 3,967 equities — today's NIFTY 500 — and the overlay
> excludes what it cannot classify, so the rule silently encoded "hold only
> companies that are in the NIFTY 500 in 2026". Rebuilding the labels to cover
> 3,110 symbols and re-running the identical rules gives **12.84% CAGR**; the
> same rules with the overlay off give **13.42% with a −15.02% drawdown**. The
> gap between 15.80% and 12.84% over the training window is the look-ahead,
> measured directly. Section 1.2 has the evidence and what follows from it.

Checks that could have failed and did not:

- **Null test.** Random entries through the identical engine return −0.023R per
  trade against the strategy's +0.353R. No lookahead is leaking through the
  machinery.
- **Survivorship.** The universe is rebuilt each session from that session's
  bhavcopy. ~~Nothing reads a present-day list.~~ **This claim was wrong.** The
  universe filter reads no present-day list, but the *sector overlay* does, and
  it is applied after the universe. See section 1.2.
- **Walk-forward.** Parameters fixed: 7.2% (2008–12), 11.8% (2013–19), 22.7%
  (2020–26). Every period profitable, every drawdown inside the band.
- **Parameter stability.** Position count and risk move results smoothly rather
  than spiking at the chosen value.

Two things this rests on that are *not* proven:

- **The sector cap is unproven.** Caps of 2/3/4/none give drawdowns of 14.1%,
  12.3%, 15.1%, 13.9% with CAGR flat throughout. That ordering is noise. The cap
  is kept for sound concentration reasons, not because the backtest justifies it.
- **Sector labels are present-day.** ~~The control says the selection effect is
  real, so expect live results slightly below backtest.~~ **This was too
  generous.** The effect was largely artifact, and live results were far below
  backtest, not slightly. Section 1.2.

### 1.2 What the out-of-sample run found, August 2026

Parameters were re-selected on data ending 2024-12-31 and applied untouched to
2025 and 2026. Features over the truncated lake match features over the full
lake row for row across all 4,441 training sessions, so no rule reads forward;
the contamination was in the *labels*, not the machinery.

| | 2025 | 2026 YTD |
| --- | --- | --- |
| the book, as configured | −3.46% | +6.20% |
| NIFTY 50 | +10.05% | −7.54% |
| broad basket, held only while regime ON | +5.06% | +5.60% |

2025 was a bull market the book missed by 13 points. The regime filter was not
at fault — simply holding the basket while the switch said *go* returned +5.06%.
Nor was it the parameters: across 49 configurations only 8 were profitable in
2025, and training CAGR correlated **−0.45** with 2025 return, so the settings
that looked best over 17 years did worst. It was the overlay, which could reach
only 500 of 3,967 names.

The labels were then rebuilt (section 5.1) and the rule re-tested on 3,110
symbols across 25 sectors:

| Variant | Train CAGR | Train DD | Calmar | 2025 | 2026 YTD |
| --- | --- | --- | --- | --- | --- |
| overlay on, unlabeled excluded | 12.84% | −13.84% | 0.93 | +3.36% | +19.89% |
| overlay on, unlabeled pass | 12.59% | −16.83% | 0.75 | +9.61% | +23.42% |
| overlay top 50%, unlabeled pass | 13.11% | −15.64% | 0.84 | +14.39% | +26.43% |
| **overlay off** | **13.42%** | −15.02% | 0.89 | **+19.16%** | +26.25% |

Better data fixes most of the damage without touching a rule — 2025 goes from
−3.46% to +3.36%. But the overlay still loses to switching it off, on training
CAGR and on 2025, and ties on 2026. That is now a finding about the rule rather
than about the data, because the data was fixed first.

Note the first row: best Calmar, best training drawdown, worst holdout. Labels
cover 96% of listed companies and 45% of delisted ones, so a rule that excludes
the unlabeled flatters the past by declining to trade companies that later died.
That asymmetry is why `require_sector_label` exists.

Two defects were found on the way and are fixed in the engine, both defaulted
off so no running book changed behaviour:

- **ETFs were tradeable.** NSE lists fund units in the `EQ` series, so `series`
  never separated them from companies; the overlay had been excluding them only
  by accident, because a fund has no industry label. With the overlay off the
  book opened six silver ETFs on one morning, sized as six independent
  positions — 52% of the period's P&L on one commodity. `equity_only` and
  `max_per_group` address the two halves of that.
- **The reference figures are stale.** `tests/test_backtest_fidelity.py` pins
  the numbers in the table above. They still hold for the shipped defaults, and
  are expected to fail the moment the overlay is switched off — which is the
  test doing its job. Re-baseline deliberately, in a commit that says so.

Reproduce with `scripts/walkforward.py`, `scripts/overlay_recheck.py` and
`scripts/label_gap.py`.

### 1.1 The cash condition

The book is only ~34% deployed on average, so the return on idle cash is the
single largest lever in the model — larger than any parameter.

| Idle cash earns | CAGR | Max drawdown |
| --- | --- | --- |
| 0% | 10.87% | −16.79% |
| 5% | 14.38% | −12.25% |
| 6% | 15.07% | −11.50% |

Validated against the real rate path: replaying 2016–2026 with the actual
`NIFTY 1D RATE INDEX` instead of a flat 5% gives 17.74% vs 17.37%, because the
true average was 5.55%. The obvious worry — that rates collapse exactly when the
strategy is in cash — does not hold: realised overnight yield was 5.67% while the
regime was OFF versus 5.49% while ON.

**Operationally this means the float belongs in an overnight or liquid instrument,
not in the broker's cash balance.** Not a credit-risk or short-duration fund; the
extra 1–2% those pay is exactly what became unredeemable in March 2020.

---

## 2. The strategy

Signals are computed on a session's close and executed at the **next** session's
open. The pipeline runs 19:45 IST, so the panel always answers "what do I do
tomorrow morning".

### 2.1 Universe — rebuilt every session from trailing data only

- Series `EQ` only; trade-to-trade and surveillance segments excluded
- 60-day median turnover ≥ ₹2 crore **and** inside the top 500 by turnover
- Unadjusted close ≥ ₹20
- At least 250 sessions of history

### 2.2 Regime — one on/off switch for the whole book

An equal-weight index of the 200 most-traded names, compared to its own 100-day
moving average. Built from the lake rather than using NIFTY, because NIFTY 50
daily history in `index_daily` only starts November 2015 while bars go back to 2007.

- **ON** → entries permitted
- **OFF** → no entries, and every open position closes at the next open

ON roughly 60% of sessions. The market returns +23%/yr annualised while ON and
−9%/yr while OFF. This is what keeps the 2008 and 2020 crashes out of the
drawdown record — it does not predict them, it takes the first leg down and exits.

### 2.3 Entry

All of:

- Close makes a new 52-week closing high
- `close > 50DMA > 150DMA > 200DMA`, and the 200DMA rising over 20 sessions
- 126-day return in the top 20% of the universe that session
- Stock's sector in the strongest 25% by 63-day composite momentum
- Fewer than 3 positions already held in that sector

Candidates are ranked by relative strength; the strongest fill first.

### 2.4 Exit — whichever comes first

- **Stop:** 3.0 × ATR(14) below entry, judged **on the close**. If the close is at
  or below it, sell at the next open.
- **Time stop:** 60 sessions (~3 months)
- **Regime flip:** everything closes at the next open
- **No trailing stop.** Tested and removed — it truncated winners and cost return.
- **Stale symbol:** no bars for 5 sessions → close at last traded price. Almost
  always a ticker rename or merger.

> The close-based stop is deliberate. It was tested against an intraday resting
> stop and is both better (18.0% vs 17.0% CAGR on the equivalent config) and
> verifiable from end-of-day data alone. The trade-off is that an overnight gap is
> taken in full — see §2.6.

### 2.5 Sizing

- Risk 0.60% of **current book equity** per trade
- `qty = risk_amount / (entry − stop)`
- At most 12 concurrent positions, 10% of equity in any one
- Never more than 5% of the stock's 20-day median turnover

Size is set by risk, not by dividing capital equally. A calm stock gets a large
position and a volatile one a small position, for identical rupee risk. On a
₹50 lakh book the budget is ₹30,000 and positions range roughly ₹1.9L–₹5.0L.

### 2.6 What the risk budget actually guarantees

Nothing, strictly. Across all 972 trades:

| Loss exceeded | Trades | Share |
| --- | --- | --- |
| 1.00R | 128 | 14.25% |
| 1.50R | 14 | 1.56% |
| 3.00R | 3 | 0.33% |

Worst single trade: −6.88R, 4.13% of the book. Average loss −0.62R, comfortably
under budget, because regime and time exits usually close a position before the
stop is reached. **Size for "typical 1R, occasionally 2R, once-a-decade 7R".**

### 2.7 Costs applied to every trade

- Buy 0.147%, sell 0.137% — brokerage, STT, exchange, stamp, GST
- 0.20% slippage per side
- Round trip ≈ 0.68%

---

## 3. Configuration — the customisability requirement

Every number in §2 is a field on one dataclass. Nothing reads a literal.

```python
# pipeline/compute/strategy/config.py
@dataclass(frozen=True)
class StrategyConfig:
    name: str = "balanced"
    # universe
    series: tuple[str, ...] = ("EQ",)
    min_turnover: float = 2e7
    top_n_turnover: int = 500
    min_price: float = 20.0
    min_history: int = 250
    equity_only: bool = False         # ISIN INE = company, INF = fund unit
    max_per_group: int = 0            # positions sharing a tracked underlying
    # regime
    regime_index_n: int = 200
    regime_ma: int = 100
    regime_exit: bool = True
    # entry
    breakout_lookback: int = 250      # 0 = all-time high
    rs_lookback: int = 126
    rs_min_pct: float = 0.80
    sector_top_frac: float = 0.25     # 0 disables the sector overlay
    require_sector_label: bool = True # False: unlabeled skips the sector test
    sector_lookback: int = 63
    max_per_sector: int = 3           # 0 = unlimited
    # exit
    atr_len: int = 14
    stop_atr: float = 3.0
    stop_on_close: bool = True
    time_stop: int = 60
    trail_atr: float | None = None    # None = no trailing stop
    stale_exit: int = 5
    # sizing
    risk_pct: float = 0.0060
    max_positions: int = 12
    max_weight: float = 0.10
    adv_cap: float = 0.05
    # frictions
    buy_charges: float = 0.00147
    sell_charges: float = 0.00137
    slippage: float = 0.0020
    cash_yield: float = 0.05
```

**Named presets** ship alongside, each a measured point on the frontier:

| Preset | risk | positions | CAGR | Max DD |
| --- | --- | --- | --- | --- |
| `conservative` | 0.40% | 10 | 10.6% | −6.2% |
| `balanced` *(default)* | 0.60% | 12 | 14.4% | −12.3% |
| `aggressive` | equal-weight 8.3% | 12 | 19.7% | −19.5% |

Rules for keeping this honest as it evolves:

1. **A config is content, not code.** Each book row stores the full config as
   JSON, so a change is visible in the data and past results stay attributable to
   the parameters that produced them.
2. **Changing a parameter invalidates nothing silently.** The backtest harness
   (§6) takes the same object, so any change can be re-validated with one command
   before it goes live.
3. **Adding a rule means adding a field**, defaulted to off, so existing books
   behave identically until deliberately switched on.

### 3.1 Capital

Capital is a **variable, not a constant**. A book opens on `--capital` (or
`DEFAULT_CAPITAL` when the flag is absent) and thereafter sizes from current
book equity, which compounds. Risk per trade therefore starts at ₹30,000 on ₹50
lakh and grows with the book.

The opening figure is read **once**, on the first run, while `strategy_books` is
still empty; from then on `strategy_books.capital` is the live number, and the
only thing that changes it is the reset in §3.2. An earlier design seeded from
`user_prefs.capital` so the terminal and the book would show one number, but the
profile field went on accepting edits long after anything read it — an inert
control that looked live. It has been removed; capital has one home, the
Strategy tab.

### 3.2 Changing a live book

Tweaking a running book is expected — that is the point of having the terminal.
Two things have to hold for the record to stay meaningful afterwards.

**Config changes are versioned, not overwritten.** Every change appends to
`strategy_config_log` and bumps `strategy_books.config_version`; every position
is stamped with the version that produced it. Otherwise a book whose risk went
from 0.60% to 0.80% halfway has a CAGR belonging to neither setting, and the
question "what did 0.60% actually do" becomes unanswerable. Results can be
sliced by version, and comparing settings properly still means running two books
side by side rather than changing one.

**Capital changes are cash flows, not returns.** Adding ₹10 lakh raises equity
by ₹10 lakh, and a CAGR read straight off the equity curve would book that as
performance. Deposits and withdrawals go to `strategy_cashflows`, the day's flow
is recorded on `strategy_state`, and reported returns are chain-linked across
it — the daily factor is `equity_end / (equity_start + flow)`, compounded. The
flow settles at the open, so it belongs in the base the day is measured against,
not subtracted from the result. That is a time-weighted return, and it is the
only version comparable to the backtest, which has no flows at all. Note what it
is a return *on*: total equity, cash included. A book a third deployed whose
holdings are up 6% has returned 2%, and the headline says 2%.

**The base is the previous session's recorded equity, not a re-derived one.**
On the rules book there is no difference — nothing touches it between runs, so
rebuilding the state reproduces exactly where the day started. A manual book is
not like that: the API adds and closes positions during the session, and by the
time the nightly job rebuilds the book the new holding is in it while the cash
that bought it is gone. Deriving the base from that state values the position at
*yesterday's* close, which puts the entire entry-to-yesterday gain in the
denominator, where it is never counted as return — a backdated trade enters the
book already up and the book never says so. Against the recorded base, a
position arriving at cost is value-neutral, so its whole P&L lands in the
session it appears. This was live for four sessions and cost the manual book
1.48 points of a 2.01% return.

**Manual entries are marked.** A position opened by hand carries
`origin = 'manual'`, so headline metrics can be reported for the rules-only
subset. Mixing discretionary trades into the record without a flag is what makes
a paper book stop being evidence.

### 3.3 Books

A *book* is one config plus its paper positions. The engine iterates over a list
of enabled books, so running a second configuration in parallel is a row in a
table, not a code change.

**Default: one book, `balanced`, filled automatically.** Automatic fills are what
make the record a test of the strategy — any discretion makes it untestable. A
`fill_mode` field (`auto` | `manual`) is defined now and left at `auto`; manual
mode is a UI affordance to add later, not a schema change.

---

## 4. Where the code lives

```
pipeline/compute/strategy/
├── __init__.py       run() — the nightly entry point
├── config.py         StrategyConfig + presets            (depends on nothing)
├── windows.py        trailing-window primitives           (numpy only)
├── rules.py          THE strategy: universe, regime, entries, exits, sizing
├── book.py           one session of the paper book: fills, ageing, exits
├── store.py          the only reader/writer of the strategy_* tables
├── data.py           MarketData, from R2 or a local mirror
└── backtest.py       book.advance() in a loop, over 18.6 years
```

`windows.py` exists because pandas is not a runtime dependency of the pipeline.
The strategy was validated with a pandas implementation of the rolling windows,
so `tests/test_windows.py` holds the numpy versions to pandas' exact semantics —
including the detail that `rolling(w, min_periods=mp)` emits a value once `mp`
observations exist, not once `w` rows have elapsed. Getting that wrong changes
every moving average and silently changes the strategy.

The dependency direction matches the rest of the pipeline: `config` knows
nothing, `rules` knows only `config`, `book` knows `rules`, `store` knows `book`.

### 4.1 One definition of the rules

`rules.py` is the single source of truth for what the strategy *is*. Both callers
import it:

- `__init__.run()` — tonight's signals and exits, for the live book
- `backtest.py` — the same functions replayed over 18.6 years

This is the point of the whole layout. If the live path and the validation path
had separate copies of the rules, they would drift, and the strategy you trade
would quietly stop being the strategy that was tested. Re-running the historical
validation must exercise *exactly* the code that produces tomorrow's signals.

`tests/test_backtest_fidelity.py` enforces it. The reference run's ten headline
metrics are checked in as literals, so any change that moves CAGR, drawdown,
trade count or expectancy fails the suite rather than passing silently. Porting
the engine into this package already tripped it once: the scratchpad derived
each stop from the *fill* price and an early draft of `rules.py` carried the
signal-day stop through instead — worth 1.5 points of CAGR. That is now the
`stop_from_fill` switch, defaulted to the validated behaviour.

---

## 5. Data model

Defined in
[`supabase/migrations/20260823120000_strategy_engine.sql`](../supabase/migrations/20260823120000_strategy_engine.sql),
which is the authoritative version — the summary here is for reading, not for
copying. Neither the pipeline nor the server issues DDL.

| Table | Holds |
| --- | --- |
| `strategy_books` | one row per book: current `config` (JSON), `config_version`, `capital`, `fill_mode` |
| `strategy_config_log` | every config the book has ever run, versioned |
| `strategy_cashflows` | deposits and withdrawals, kept out of the return series |
| `strategy_state` | daily regime, equity, cash, deployed, net flow — also the equity curve |
| `strategy_signals` | tomorrow's ranked candidates, already sized, with fill status |
| `strategy_positions` | the paper book; each trade stamped with `config_version` and `origin` |

**Types follow the baseline, not native Postgres**, and deliberately:

- **Dates are `TEXT`** (`'YYYY-MM-DD'`). A `DATE` column returns through node-pg
  as a JS `Date` and JSON-serialises to `2026-08-21T00:00:00.000Z`, which the UI
  would have to re-trim. Every other table already stores ISO strings.
- **Money is `REAL`, not `NUMERIC`.** node-pg returns `NUMERIC` as a *string* to
  preserve precision; the UI does arithmetic on these, and a paper book at rupee
  scale is far inside a double's range.
- **The config blob is `TEXT` holding JSON**, matching `breadth_daily.data`.

Verified by applying all three migrations in order to a throwaway Postgres 16:
clean apply, idempotent on re-run, and accepts realistic rows.

Storing `stop_pct` and `risk_amount` on the signal is deliberate: without them you
cannot tell from the UI why one position is ₹1.9L and another ₹3.8L, and the
sizing stops being checkable. `init_stop` is kept alongside the live `stop` so R
multiples stay comparable after the stop has moved.

### 5.1 Industry classification — `curated/instruments/industry.parquet`

Built by `python -m pipeline industry`, keyed by **ISIN** rather than ticker so a
company that changed symbol still resolves. Columns: `isin`, `scrip_code`,
`bse_symbol`, `name`, `status`, `industry` (basic industry), `macro` (sector),
`group` (the level above sector, NSE only) and `source`.

Two sources, in this order, and the order is the point:

1. **BSE scrip master** — Active, Delisted and Suspended. A label set covering
   only survivors turns any filter built on it into a survivorship screen, and
   BSE is the exchange that still knows the dead. 2,687 labels.
2. **NSE**, via `getDetailedScripData`, for still-listed names BSE could not
   label. NSE's own `/api/quote-equity` — the endpoint its quote page calls — is
   refused at Akamai's edge for anything that is not a browser, which is why a
   different endpoint is used. 423 labels.

Both exchanges publish the same NIC-derived vocabulary, so the two merge without
a mapping layer. The NIFTY 500 constituent file remains as a fallback for a lake
that has not run the ingest yet, and the two vocabularies are **never mixed** —
blending them would split one sector across two labels and corrupt the breadth
counts.

| | labelled | coverage |
| --- | --- | --- |
| lake equities with an ISIN | 3,002 / 3,719 | 81% |
| still trading | 2,489 / 2,583 | 96% |
| delisted | 513 / 1,136 | 45% |

The last two rows are the reason `require_sector_label` exists. Coverage is far
better for companies that survived, so a rule that excludes the unlabeled is a
survivorship screen wearing a sector filter's clothes. Set the flag false and an
unlabeled name skips the sector test instead of failing it, which errs toward
the dead — the conservative direction.

Refresh is a snapshot, overwritten in place like `constituents.parquet`, and
resumable: rows already stored are not re-fetched unless `--refresh` is passed.

---

## 6. The nightly job

Appended to the `eod` chain in `pipeline/jobs.py`, after `publish`, and exposed as
`python -m pipeline strategy` for manual runs. Idempotent like every other step: a
re-run for the same session converges rather than double-counting.

For each enabled book, in order:

1. **Load** open positions and config from Postgres.
2. **Fill** yesterday's `pending` signals at today's open, subject to slot,
   sector-cap, ADV and cash limits. Mark each `filled` or `skipped` with a reason.
3. **Age** every open position: increment `bars`, refresh the last traded price.
4. **Exit** by the §2.4 ladder — stop on close, time stop, regime flip, stale.
   Exits queue for the next open, exactly as backtested.
5. **Mark to market** and write `strategy_state`.
6. **Emit** tomorrow's ranked candidates into `strategy_signals`, already sized.

Ordering matters and mirrors the backtest exactly: fills happen before exits are
evaluated, and both happen before new signals are generated.

**Re-running a session is a no-op.** The job refuses to advance a session it has
already recorded, so a repeated nightly run converges rather than double-counting.
`--force` re-advances anyway, for repairing a bad run; it resumes cash from the
*previous* session's row so the day's interest is not accrued twice, and a forced
re-run reproduces the original figures exactly.

**Signals can only be filled by a later session.** `load_pending_signals` bounds
on `date < session`. Without that bound, re-advancing a session would fill that
session's own signals at its own open — an open that happened hours before the
close which produced them. Testing replay is what surfaced it; nothing about the
output looked wrong.

**The step fails soft.** It is wrapped like `reference.refresh()` already is, so a
fault in the strategy engine logs and continues rather than taking down the
breadth, sector and metrics publish the terminal depends on. A paper book is not
worth breaking the product for.

---

## 7. API

One route, behind `requireAuth` like everything else:

```
GET /api/strategy/summary?book=balanced
```

Returns the latest `strategy_state`, open positions with live P&L and an action
flag, tomorrow's ranked candidates with sizing, and the paper equity curve.
`GET /api/strategy/books` lists enabled books so the UI can offer a switcher when
more than one exists.

No write routes in phase one. The book is advanced by the pipeline, not by the
browser — which is what keeps the record clean.

---

## 8. UI

A new `StrategyTab`, alongside the existing tabs.

- **Regime banner** — ON/OFF, how long, the index against its moving average.
  This is the first thing to read; when it is OFF nothing else matters.
- **Tomorrow's orders** — ranked candidates: symbol, sector, RS, reference close,
  stop, **stop distance %**, quantity, position value, ₹ at risk. Headed "buy at
  tomorrow's open", because that is what was tested.
- **Open positions** — entry, current, stop, bars held, sessions until the time
  stop, P&L in ₹ and R, and a `HOLD` / `SELL AT OPEN` badge.
- **Book summary** — equity, cash, deployed %, positions used of maximum.
- **Paper equity curve** — against the same benchmark used in the backtest.

---

## 9. Operational notes

**Timing.** The pipeline runs 19:45 IST on the day's close; every backtested fill
is the next session's open. Acting intraday on the same signal is untested
behaviour, not a shortcut.

**Starting state.** The book starts flat from the day the migration is applied.
Nothing is carried over from the backtest, so everything you see forward is
observed rather than inherited.

**Stops are not broker orders.** Because the stop is judged on the close, there is
no resting stop-loss to place. The panel tells you to sell at the next open when a
close breaches the stop.

**Deployment.** Supabase and Vercel are wired to the repo through their GitHub
integrations, so merging to `main` applies pending migrations and ships the
frontend. Nothing in `.github/workflows` does this — `checks.yml` runs ruff and
mypy, `eod.yml` runs the pipeline — so the migration step is invisible from the
repo and is worth remembering when reasoning about deploy order.

---

## 10. Open decisions

| # | Question | Default if unanswered |
| --- | --- | --- |
| 1 | One book or two running in parallel? | One (`balanced`); a second is a table row |
| 2 | Automatic or manual fills? | `auto`; manual trades are recorded with `origin='manual'` and reported separately |
| 3 | ~~Who applies the migration?~~ | **Resolved** — Supabase's GitHub integration applies it on merge to `main` |

---

## 11. Out of scope, and known limits

**No fundamentals, no news.** The lake holds bhavcopy bars, index closes,
corporate actions and constituent lists — prices and volumes only. There are no
earnings or valuation figures anywhere in it, and `news_articles` is a live
Marketaux cache with no history and a 100-request daily cap. A combined
technical/fundamental/news strategy is a data-ingestion project first.

**No symbol-change map.** A renamed company reads as a death and a birth —
`BAJAUTOFIN`→`BAJFINANCE`, `AEGISCHEM`→`AEGISLOG`. A `curated/symbol_changes`
dataset would improve every long-history analysis, not just this one. Until then
`stale_exit` closes the orphan at its last traded price.

**No execution.** No broker integration and none planned. This produces
instructions and keeps a paper record; placing orders is manual.

**Capacity.** Position size is capped at 5% of a stock's 20-day median turnover.
Across the full backtest, with the book growing from ₹50 lakh to ₹6.08 crore,
zero trades were skipped for liquidity. Beyond roughly ₹10 crore this universe
stops being deep enough.
