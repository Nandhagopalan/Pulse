# Pulse — Data map

Every dataset Pulse holds: where it physically sits, what shape it has, which
code writes it and which code reads it. This is the reference to reach for when
the question is *"where does X live"* or *"who produces this field"*.

For why storage is split this way, see
[architecture.md §3](architecture.md). For the strategy tables' design rationale
see [strategy-engine.md §5](strategy-engine.md). Where this file and the code
disagree, the code wins and this file is a bug.

---

## 0. The one rule

**Only derived state reaches the database the UI queries.**

| Store | Holds | Size | In the request path? |
| --- | --- | --- | --- |
| Cloudflare R2 | ~19 years of daily bars as Parquet, the raw vendor files, and all reference data | ~250 MB zstd Parquet, ~8M bars | No — pipeline only |
| DuckDB | Nothing. A library, not a service: it runs inside the nightly job, scans R2 over the S3 API, and exits | — | No |
| Supabase Postgres | The latest computed state per symbol, the paper book, and all user state | A few MB | **Yes — the only store the API touches** |

Reference data the request path never joins against — instruments, index
constituents, corporate actions, industry labels — stays on R2 and is joined in
DuckDB at compute time. ~8M rows would be roughly 1.2 GB in Postgres, past
Supabase's free tier; as zstd Parquet on R2 it is ~250 MB and R2 charges no
egress.

---

## 1. Cloudflare R2 — the lake

Bucket: `R2_BUCKET_NAME`, default `pulse-terminal`. Endpoint
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`. Accessed two ways, both in
[`pipeline/sources/r2.py`](../pipeline/sources/r2.py): **boto3** for object-level
work (does this key exist, put this zip, list a prefix) and **DuckDB** for
anything that reads or writes Parquet, so scans stay columnar and push down
predicates instead of dragging whole files through Python.

Key layout constants live in [`pipeline/config.py`](../pipeline/config.py).

### 1.1 `raw/` — vendor bytes, untouched

| Key | Source | Written by |
| --- | --- | --- |
| `raw/nse/bhavcopy/YYYY/YYYY-MM-DD.zip` | `nsearchives.nseindia.com` — the UDiFF `BhavCopy_NSE_CM_*.csv.zip` from Jul 2024, the legacy `cmDDMONYYYYbhav.csv.zip` before it | `ingest/backfill.py` |
| `raw/nse/index_close/YYYY/YYYY-MM-DD.csv` | `ind_close_all_DDMMYYYY.csv` | `ingest/backfill.py` |
| `raw/nse/corp_actions/YYYY.json` | `www.nseindia.com` corporate actions feed, one file per year | `ingest/corporate_actions.py` |

This layer exists so **every derived dataset can be rebuilt without asking NSE
again**. Their archives rate-limit hard, block datacenter IPs, and have retired
paths before. It is also what makes the backfill resumable: a session already in
R2 is read back from R2 rather than re-fetched, so a re-run costs no archive
traffic.

### 1.2 `curated/` — the queryable Parquet lake

One file per **year**, not per session. A full scan is then ~19 range requests
instead of ~4,700 round-trips, which on a remote object store is the whole
difference.

#### `curated/daily/year=YYYY/data.parquet` — equity bars

Written by `ingest/backfill.py` (`DAILY_SCHEMA`), sorted by `(date, symbol)`.

| Column | Type | Note |
| --- | --- | --- |
| `symbol` | string | NSE ticker |
| `date` | date32 | session |
| `series` | string | `EQ`, `BE`, … |
| `isin` | string | the stable key; a company that changes ticker keeps its ISIN |
| `open` `high` `low` `close` | float64 | **raw**, unadjusted |
| `prev_close` | float64 | as NSE served it — *not* restated on ex-dates (§4) |
| `volume` | int64 | |
| `traded_value` | float64 | |
| `trades` | int64 | |

Read by: `compute/analytics.py`, `compute/strategy/data.py`, `audit.py`,
`verify.py`, `ingest/industry.py`, `ingest/corporate_actions.py`.

#### `curated/index_daily/year=YYYY/data.parquet` — index bars

`index_name` (string), `date` (date32), `open`, `high`, `low`, `close` (float64).
Written by `ingest/backfill.py` (`INDEX_SCHEMA`); read by `compute/analytics.py`,
which publishes the tail into `index_bars`.

#### `curated/corporate_actions/actions.parquet` — adjustment factors

Written by `ingest/corporate_actions.py`. One row per **event** (symbol × ex-date),
not per filing.

| Column | Type | Note |
| --- | --- | --- |
| `symbol` | string | |
| `ex_date` | date32 | the date the tape actually moved, after slack correction |
| `factor` | float64 | `k`; `adjusted = raw / k` for bars *before* `ex_date`, `volume * k` |
| `kind` | string | `bonus` \| `split` \| `bonus+split` |
| `status` | string | `verified` \| `unverified` \| `no_bars` \| `not_adjusting` |
| `implied` | float64 | the ratio the tape actually showed |
| `subject` | string | the raw NSE label, kept for audit |
| `source_ex_date` | date32 | what the filing said, before slack correction |
| `applied` | bool | whether the adjustment is used downstream |

Consumed through `ca.adjusted_bars_cte()`, which every reader of adjusted prices
goes through — analytics, the strategy engine, the audit and `verify`.

#### `curated/instruments/constituents.parquet` — index membership

`index_name`, `symbol`, `name`, `industry`, `isin` (all string). Written by
`ingest/reference.py` from NSE's `ind_nifty*list.csv` files for NIFTY 50,
NIFTY 500, MIDCAP 100, SMLCAP 100, BANK and IT.

These files describe **current** state — there is no archive of "who was in
NIFTY 500 in 2011" — so this is a snapshot, overwritten in place on every run,
unlike the append-only bar datasets. That is exactly why it cannot be the sector
source for a backtest (§1.3).

#### `curated/instruments/industry.parquet` — the sector source of truth

Written by `ingest/industry.py`, keyed by **ISIN**.

| Column | Note |
| --- | --- |
| `isin` | the key |
| `scrip_code`, `bse_symbol`, `name`, `status` | BSE identifiers; `status` covers Active / Delisted / Suspended |
| `industry` | basic industry, e.g. "Auto Components & Equipments" |
| `macro` | macro sector, e.g. "Automobile and Auto Components" — this is what the UI calls a sector |
| `group` | the level above sector; NSE only |
| `source` | which exchange answered — provenance, not decoration |

Built from **BSE's scrip master first** (2,687 labels, and it keeps the dead),
then NSE's `getDetailedScripData` for still-listed names BSE could not label
(423). The order is the point: a label set covering only survivors turns any
filter built on it into a survivorship screen. Coverage is ~81% of the lake's
equities *and* the same ~81% of the delisted ones, so what stays unlabeled does
not correlate with survival.

Building it is a manual job (a few thousand exchange calls). Only
`industry.normalize()` — a read-modify-write of this one object that reconciles
label spellings — runs nightly. The vocabulary itself is pinned in
[`ingest/taxonomy.py`](../pipeline/ingest/taxonomy.py): 22 canonical macro
sectors plus a legacy map for NSE's pre-2018 names.

---

## 2. Supabase Postgres — the hot state

Schema: [`supabase/migrations/`](../supabase/migrations), applied with
`supabase db push` in production and by `supabase start` locally, so dev and
production are built from identical SQL. **Neither the pipeline nor the server
issues DDL.**

Type conventions are uniform and deliberate: dates are `TEXT` (`'YYYY-MM-DD'`),
JSON payloads are `TEXT` holding JSON, quote-like prices are `REAL`, and ledger
money in the `strategy_*` tables is `DOUBLE PRECISION`.

### 2.1 Published by the pipeline

Writer: [`pipeline/compute/publish.py`](../pipeline/compute/publish.py) — the
only writer to Supabase in the bar pipeline. Reader: `server/src/api.ts`.

| Table | Key | Payload | Write mode |
| --- | --- | --- | --- |
| `breadth_daily` | `date` | `data` — the breadth JSON below | upsert |
| `sector_scores` | `(date, sector)` | `data` — one sector's JSON | **delete-then-insert for the session** |
| `stock_metrics` | `(date, symbol)` | `data` — one symbol's JSON | **delete-then-insert for the session** |
| `index_bars` | `(index_name, date)` | `open` `high` `low` `close` (REAL) | upsert |
| `stock_candles` | `symbol` | `date`, `data` (columnar JSON), `updated_at` | upsert |
| `fii_dii` | `(date, category)` | `buy` `sell` `net` (REAL) | upsert |
| `meta` | `key` | `value` — `last_ingested_session`, `last_analytics_date` | upsert |
| `ingest_log` | (none) | `ts` `job` `date` `status` `detail` | append |

Sectors and stock metrics are **deleted and reinserted** rather than upserted, so
a symbol that dropped out of the universe today cannot linger with yesterday's
numbers attached to today's date.

Writes go through a direct `psycopg` connection in batches of 500 rather than
PostgREST: a few thousand upserts in one transaction take seconds this way and
minutes through the REST API.

#### `stock_metrics.data` — per symbol, latest session

Produced by `analytics.compute()`. `sym`, `sector`, `price`, `chg1d`, `chg1w`,
`distATH`, `dist52`, `isATH`, `is52`, `wkBreak`, `athSince`, `athDate`, `e10`,
`e20`, `e50`, `e200`, `rs` (63-session relative strength vs NIFTY 500, falling
back to NIFTY 50), `volume`, `deliveryPct`, `turnover`, `trendBreak`. Symbols
with a weekly trendline break carry extra break fields; the other ~2,800 rows
stay the size they were.

`isATH` is `distATH < 0.4`, `is52` is `dist52 < 0.5` — both measured **from the
close**, never from intraday highs (§4).

#### `breadth_daily.data` — one session

`date`, `universe`, `advances`, `declines`, `unchanged`, `newHighs`, `newLows`,
`athCount`, `emaVals` (percent above each EMA), `emaHist` (120 sessions of the
same for e20/e50/e200), `adDaily` and `nhDaily` (90 sessions), `series`
(45-session tails of `newHighs`, `newLows`, `up20`, `up30`, `up4vol`, `down4vol`,
`netHL`), `dates`.

`HIST = 120` sessions of history are published; the compute window is
`WINDOW = 520` so every point in that history has a full 52-week lookback.

#### `sector_scores.data`

`name`, `count`, `adv`, `dec`, `dmaPct`, `newHighs`, `wk`, `score`. Symbols
labeled `Other` are excluded. The score is
`dmaPct*0.5 + clamp((wk+3)*5, 0, 30) + min(20, newHighs/count*220)`, clamped to
2..99.

#### `stock_candles.data`

`CANDLES = 500` split-adjusted bars per active symbol, stored **columnar** —
`{d:[], o:[], h:[], l:[], c:[], v:[]}` — which is the same data at roughly a
third of the JSON bytes.

This table is a deliberate cache, not a lake read: the chart never asks for more
than 500 bars, so caching that tail keeps `GET /api/stocks/:sym/candles` a single
indexed row read. Serving it from Parquet would mean a DuckDB scan per chart open.

#### `fii_dii`

Scraped by `publish.py::publish_flows` from `www.nseindia.com/api/fiidiiTradeReact`,
**not** derived from the lake: NSE publishes only a rolling two-day window with no
archive, so there is nothing to backfill. Best-effort — that host blocks
datacenter IPs aggressively, so a failure logs to `ingest_log` and returns rather
than failing the nightly chain over a sidebar.

> The baseline migration's comment calls this "written by the Node server
> (scheduler.ts)". That is stale: the scheduler was deleted when the server
> stopped ingesting, and `publish.py` owns this table now.

### 2.2 Written by the strategy engine

Writer: [`pipeline/compute/strategy/store.py`](../pipeline/compute/strategy/store.py)
— the only reader/writer of these tables on the pipeline side. Reader:
`server/src/api.ts` (`/api/strategy/*`), which also writes positions for the
manual book.

| Table | Key | Holds |
| --- | --- | --- |
| `strategy_books` | `id` | one row per book: `config` (JSON), `config_version`, `capital`, `fill_mode` (`auto`\|`manual`), `enabled`, `started_on` |
| `strategy_config_log` | `(book_id, version)` | every config the book has ever run, with `note` |
| `strategy_cashflows` | `id` | deposits/withdrawals — kept out of the return series |
| `strategy_state` | `(book_id, date)` | the daily equity curve: `regime_on`, `ew_index`, `ew_ma`, `universe_n`, `equity`, `cash`, `deployed`, `n_open`, `net_flow`, `twr_factor` |
| `strategy_signals` | `(book_id, date, symbol)` | tomorrow's ranked, pre-sized candidates: `rank`, `ref_close`, `stop`, `stop_pct`, `atr`, `rs_pct`, `sector`, `turnover_20d`, `qty`, `position_value`, `risk_amount`, `status`, `skip_reason` |
| `strategy_positions` | `id` | the paper book: `config_version`, `origin`, `entry_date`, `entry_px`, `qty`, `init_stop`, `stop`, `r_per_share`, `last_px`, `bars`, `stale`, `status`, `pending_exit`, `exit_*`, `pnl`, `r_multiple` |

Two things that look redundant and are not: `stop_pct` and `risk_amount` are
stored on the signal so you can tell from the UI *why* one position is ₹1.9L and
another ₹3.8L — without them the sizing stops being auditable. `init_stop` is
kept alongside the live `stop` so R multiples stay comparable after the stop
ratchets.

`pending_exit` exists because an exit decided on tonight's close is executed at
tomorrow's open, so the decision has to survive the gap between two nightly runs.

`twr_factor` is the daily return **excluding** cash flows — a deposit must never
read as performance. It is measured against the *recorded* equity of the
preceding session, not against re-derived state, because a manual book's
positions arrive through the API between runs
([migration `20260828120000`](../supabase/migrations/20260828120000_twr_from_recorded_base.sql),
`tests/test_manual_book_return.py`).

### 2.3 Written by the server

| Table | Key | Holds |
| --- | --- | --- |
| `users` | `id` = `provider:sub` | Keyed on the Google subject id, which is stable — emails get reassigned |
| `sessions` | `sid` | 30-day TTL; identity only, no third-party token |
| `user_watchlist` | `(user_id, symbol)` | capped at 200 symbols |
| `user_prefs` | `user_id` | **dead.** Held `capital`, `riskPct`, `maxPos` until the strategy book took over sizing; no reader or writer remains |
| `news_articles` | `(id, symbol)` | Marketaux cache — one article can be tagged to several symbols |

### 2.4 Indexes

`stock_metrics(date)`, `sector_scores(date)`, `index_bars(index_name, date DESC)`,
`news_articles(symbol, published_at DESC)`, `user_watchlist(user_id)`,
`strategy_positions(book_id, status)`, `strategy_positions(book_id, symbol, status)`,
`strategy_signals(book_id, date DESC)`, `strategy_state(book_id, date DESC)`,
`strategy_cashflows(book_id, date)`.

### 2.5 What was deliberately removed

`20260808120100_drop_unused.sql` dropped `corporate_actions`, `daily_bars`,
`index_membership`, `instruments`, `news_interest` and `watched_symbols`. The
first four are the lake's job; the last two were superseded by per-user tables.
Do not reintroduce bar or reference tables here — that is the decision keeping
this database inside the free tier.

---

## 3. Who reads what

| Consumer | Reads |
| --- | --- |
| `GET /api/market/summary` | `breadth_daily`, `sector_scores`, `stock_metrics`, `index_bars`, `fii_dii`, `meta` for the latest session |
| `GET /api/stocks/:sym/candles?n=` | `stock_candles` (one indexed row); indices resolve from `index_bars` |
| `GET /api/strategy/summary` | all six `strategy_*` tables |
| `GET /api/news` | `news_articles`, with the account's watchlist read **server-side** — a client cannot direct the shared Marketaux budget at symbols nobody watches |
| `GET /api/status` | `meta`, `ingest_log`, coverage counts |
| `compute/analytics.py` | `curated/daily`, `curated/index_daily`, `curated/corporate_actions`, `curated/instruments/*` — plus a live NSE fetch for delivery percentages |
| `compute/strategy/data.py` | `curated/daily` + `curated/corporate_actions` + `curated/instruments/industry.parquet` |
| `audit.py`, `verify.py` | `curated/daily` + `curated/corporate_actions` |

---

## 4. Corporate actions — the adjustment contract

Splits and bonuses re-base a price overnight. Uncorrected, RELIANCE looks like it
fell 50% on 2017-09-07 and has sat "90% below its all-time high" ever since,
poisoning every ATH, EMA and 52-week figure downstream.

**Source of truth** is NSE's corporate actions feed, which states the action
outright (`Bonus 1:1`, `Face Value Split … From Rs 10/- To Rs 2/-`). Each parsed
ratio is then **verified against the tape**: the close-to-close gap at the
ex-date must agree with the stated ratio, or the event is recorded but not
applied. That catches mis-parsed labels, actions announced but never executed,
and ex-dates recorded a session off (`EX_DATE_SLACK = 7`).

**The unit is the event, not the filing.** A company declaring a bonus and a
split effective the same day files two rows whose factors multiply — AHCL's
2026-04-24 is `Bonus 1:1` next to a 10-to-2 split, a 10x re-basing that neither
row states alone and that verifying rows one at a time rejects twice over. There
are 33 such events over 19 years.

**Three factor bands**, because one pair cannot do three jobs: what a single
filing may state (`MIN_FACTOR`/`MAX_FACTOR` = 0.05/100), what a compounded event
may reach (`MAX_EVENT_FACTOR`; SARVESHWAR is k=30), and how wide the tape scan
looks (`MIN_GAP_FACTOR`/`MAX_GAP_FACTOR` = 0.004/250, because an ETF unit split
re-bases ~100x). The narrowest band otherwise wins everywhere and filters out the
evidence for the largest events before it can be weighed.

**Convention:** `adjusted = raw / k` for bars *before* the ex-date, `volume * k`.
Factors compound — two 1:1 bonuses give k = 4 on the oldest bars. **Raw bars are
never mutated**; adjustment happens at compute time via
`ca.adjusted_bars_cte()`.

**Not adjusted:** demergers and schemes of arrangement. They move the price for
real, but no ratio is derivable from the filing, so they are recorded with
factor 1.0 and `status = not_adjusting` rather than guessed at — precisely so the
audit can point at them.

> A plausible-looking shortcut does **not** work here. The bhavcopy carries a
> `PREVCLOSE` column, but NSE does not restate it on an ex-date — on the RELIANCE
> bonus it reads the unadjusted 1645.40. Any detector built on "stored close vs
> official previous close" silently finds nothing.

### 4.1 The audit — checking the output, not the input

`corporate_actions` checks its **inputs**, and answers a disagreement by applying
nothing — which is safe and completely silent. `python -m pipeline audit` asks
the opposite question of the **output**: does the adjusted history still contain
cliffs that nothing explains? A close that falls 44% in one session
(`RESIDUAL_FALL = 1.8`) and stays there has had a split or bonus go unapplied,
whatever the feed says.

Every cliff is explained from data where possible, and only what survives is
reported:

| verdict | count | meaning |
| --- | --- | --- |
| **scheme** / **rights** | 46 | the feed states a demerger, scheme of arrangement or discounted rights issue |
| **fund unit** | 50 | ETF and mutual-fund units (ISIN `INF…`); NSE's action feed is an *equity* feed, so GOLDBEES' ~100x has no filing to find |
| *(dropped)* | — | round trips (a move the next fortnight undoes is a trade), and anything under ₹1 where one tick is 100% |
| **accepted** | 7 | reviewed by hand in [`pipeline/accepted_residuals.json`](../pipeline/accepted_residuals.json) |
| **new** | 0 | the alarm — fails the run |

Steady state is 103 residuals and exit 0. Deriving the first buckets rather than
listing them is not tidiness: the hand-written list came first and was actively
dangerous — eleven of its seventy-eight rows were real unapplied splits (KCP,
GREAVESCOT, MANAPPURAM) mistaken for demergers and suppressed in perpetuity by
the very file meant to reduce noise.

---

## 5. Recomputing from scratch

The dependency order, if the lake ever has to be rebuilt:

1. `python -m pipeline backfill` — NSE archives → `raw/` and `curated/daily`,
   `curated/index_daily`. 1–3 hours, resumable, safe to interrupt. Years whose
   Parquet exists are skipped; `--force` rewrites them.
2. `python -m pipeline reference` — `curated/instruments/constituents.parquet`.
3. `python -m pipeline industry` — `curated/instruments/industry.parquet`. A few
   thousand exchange calls; rows already stored are not re-fetched without
   `--refresh`.
4. `python -m pipeline actions` — `curated/corporate_actions/actions.parquet`.
   Cached years come from `raw/nse/corp_actions/`; `--refresh` re-fetches.
5. `supabase db push` — the Postgres schema.
6. `python -m pipeline publish` — compute and upsert the snapshot.
7. `python -m pipeline audit` — confirm nothing unexplained survived.

Only steps 1, 4, 6 and 7 run nightly (plus `industry --normalize`, which fetches
nothing). Step 3 is manual by design — it is the expensive one, and its output
changes only when companies list or delist.

`--local DIR` mirrors the object store to disk so the one expensive pass over
NSE's archives can be captured once, inspected, and pushed to R2 afterwards with
`python -m pipeline sync --local DIR`. `--no-r2` works against that mirror alone.
