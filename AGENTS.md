# AGENTS.md

Orientation for coding agents working in this repo. Read this first; it exists so
you do not have to scan the tree to find out where anything lives.

Deeper references, in the order you will usually want them:

| Doc | What it answers |
| --- | --- |
| [docs/data-map.md](docs/data-map.md) | Every dataset: exact R2 keys, Parquet schemas, Supabase tables and columns, who writes each and who reads it |
| [docs/architecture.md](docs/architecture.md) | How the system is built and deployed, and why each decision went the way it did |
| [docs/strategy-engine.md](docs/strategy-engine.md) | The rules-based swing engine, its parameters and its paper book |
| [docs/nifty-options-strategies.md](docs/nifty-options-strategies.md) | NIFTY options strategy playbook: each strategy as simulable rules and adjustments, with the data and simulation readiness each needs |
| [docs/covered-call-backtest.md](docs/covered-call-backtest.md) | NIFTYBEES covered call: 5,376 variants tested on 2011–22 and 2024–26, the rules of the one that held up, and why a butterfly is the wrong hedge |
| [pipeline/README.md](pipeline/README.md) | The pipeline in its own words — commands, corporate actions, the audit |
| [server/README.md](server/README.md) | The Node backend |

---

## 1. What Pulse is

A swing-trader terminal for NSE (India): market breadth, sector strength,
highs/breakouts, drawdown structure, watchlists and a paper strategy book,
gated behind Google sign-in.

**Every price in Pulse is an end-of-day close.** There is no intraday feed, no
broker link and no live quote path — the numbers advance once a night when the
pipeline runs. This is a design decision (architecture.md §13.1–13.2), not a gap.
Do not add a "live price" anything without reading that section first.

## 2. Repo map

| Path | What it is | Language / runtime |
| --- | --- | --- |
| `pipeline/` | The batch pipeline: NSE → R2 Parquet lake → DuckDB → Supabase | Python 3.12, run with `uv` |
| `src/` | React terminal UI (Breadth, Charts, Sectors, Highs, Drawdown, Watchlist, News, Strategy tabs) | Vite + React 19 |
| `server/` | Node backend: Google SSO + read-only REST API. No build step, no scheduler, no ingest | Node ≥ 23, native TS type stripping |
| `api/pulse.ts` | Vercel adapter — hands requests to the same router as `server/` | |
| `supabase/migrations/` | The database schema. The **only** place DDL is issued | SQL |
| `tests/` | pytest, pipeline only | |
| `scripts/` | Research studies behind the strategy — see [scripts/README.md](scripts/README.md). Not part of the nightly path | Python |
| `.github/workflows/` | `eod.yml` (nightly pipeline) and `checks.yml` (ruff + mypy + pytest) | |

**`api/` holds one 43-line file and is load-bearing.** It looks empty and is not:
in production the *entire* backend is that single Vercel function, which builds
`server/`'s router once per instance and dispatches to it. `vercel.json` rewrites
`/api/*`, `/auth/*` and `/health` to it. Deleting it takes the API offline while
the static site keeps serving, which is exactly the failure mode that is hardest
to spot (architecture.md §9.2, §10.1).

Nothing in `scripts/` runs in production, and `pipeline/` never imports from it.
It is the evidence behind `docs/strategy-engine.md` §1 — three of those studies
are cited there by name as the way to reproduce the findings.

## 3. Where the data sits — the short version

Three stores, split on one rule: **only derived state reaches the database the UI
queries.** [docs/data-map.md](docs/data-map.md) has the exhaustive version.

### Cloudflare R2 — the lake (~19 years, ~8M bars, ~250 MB zstd Parquet)

```
raw/nse/bhavcopy/YYYY/YYYY-MM-DD.zip        vendor bytes, exactly as served
raw/nse/index_close/YYYY/YYYY-MM-DD.csv
raw/nse/corp_actions/YYYY.json
curated/daily/year=YYYY/data.parquet        normalized equity bars, one file per year
curated/index_daily/year=YYYY/data.parquet  index OHLC
curated/corporate_actions/actions.parquet   splits/bonuses → adjustment factors
curated/instruments/constituents.parquet    today's index membership + sector
curated/instruments/industry.parquet        ISIN-keyed industry/sector for the whole lake
```

The `raw/` layer exists so every derived dataset can be rebuilt without asking
NSE again — their archives rate-limit hard, block datacenter IPs, and have
retired paths before. One Parquet per **year** (not per session) keeps a full
scan at ~19 range requests instead of ~4,700 round-trips.

R2 is never in the request path. Only the pipeline reads it.

### DuckDB — nothing

It is a library, not a service. It runs inside the nightly job, scans the Parquet
on R2 over the S3 API, emits ~2,500 rows and exits. There is nothing to host.

### Supabase Postgres — the hot state (a few MB)

Nineteen tables, three groups:

- **Published by the pipeline** (`pipeline/compute/publish.py`): `breadth_daily`,
  `sector_scores`, `stock_metrics`, `index_bars`, `stock_candles`, `fii_dii`,
  `meta`, `ingest_log`.
- **Written by the strategy engine** (`pipeline/compute/strategy/store.py`):
  `strategy_books`, `strategy_state`, `strategy_signals`, `strategy_positions`,
  `strategy_config_log`, `strategy_cashflows`.
- **Written by the server** (`server/src/`): `users`, `sessions`,
  `user_watchlist`, `user_prefs` (dead — no reader or writer remains),
  `news_articles`.

Reference data the request path never joins against (instruments, constituents,
corporate actions) stays on R2 and is joined in DuckDB at compute time. If you
are about to add a table of daily bars to Postgres, you are about to undo the
one decision that keeps this inside the free tier.

## 4. How the nightly pipeline works

`.github/workflows/eod.yml` runs `python -m pipeline eod` at 14:15 UTC (19:45 IST)
on weekdays, an hour after NSE publishes. The chain is `pipeline/jobs.py::eod`:

| # | Step | Module | On failure |
| --- | --- | --- | --- |
| 1 | Refresh index constituents + sector map | `ingest/reference.py` | **soft** — continues on the cached map |
| 2 | Reconcile stored industry labels (no fetching) | `ingest/industry.py::normalize` | **soft** — uses labels as stored |
| 3 | Ingest the open year's sessions (`force=True`) | `ingest/backfill.py` | **hard** |
| 4 | Rebuild corporate actions for the current year | `ingest/corporate_actions.py` | **soft** — last good dataset stands |
| 5 | Compute the snapshot | `compute/analytics.py::compute` | **hard** |
| 6 | Upsert into Supabase + scrape FII/DII | `compute/publish.py::run` | **hard** (the FII/DII scrape itself is soft) |
| 7 | Advance the paper book | `compute/strategy/` | **soft** — a paper book is not worth the night's publish |
| 8 | Audit the adjusted history | `audit.py` | **hard on a finding, soft on an outage** |

Every step is idempotent: a re-run converges rather than double-counting, and a
missed day is fixed by running it again. Sessions already cached in R2 are never
re-fetched from NSE.

**Step 8 runs after publish on purpose.** The snapshot is already in Supabase by
the time the audit can fail, so raising costs nobody their data; what it buys is
the one notification channel this repo has — a failed scheduled workflow, which
GitHub mails. An *outage* (unreachable lake) is not a finding and must not fail
the run, or the mail gets filtered and the next real finding goes unread.

### What `analytics.compute()` actually does

One DuckDB pass plus a numpy pass, split on what each is good at:

- **DuckDB, over the whole 8M-bar lake** — all-time highs, first-listed dates,
  the corporate-action adjustment join, and a ~210-week aggregation for weekly
  trendline structure. Anything "all-time" has to see every bar.
- **numpy, over a 520-session window** (`WINDOW = 520`) — EMAs (10/20/50/200),
  52-week extremes, breadth counters, relative strength, sector composites.
  Sequential per-symbol state, trivial as a vectorised loop over a 520 × ~3000
  matrix (~50 MB of RAM).

It returns one dict: `{date, breadth, sectors, stocks, indices, candles}`.
`publish.py` is the only thing that writes it to Postgres, and it is the only
writer to Supabase in the whole pipeline.

## 5. Commands

```bash
# Pipeline (uv syncs dependencies automatically; uv.lock pins what CI installs)
uv run python -m pipeline backfill          # 2007 → today, NSE archives → R2 (~1–3 h, resumable)
uv run python -m pipeline reference         # index constituents + sector map
uv run python -m pipeline industry          # ISIN-keyed industry labels (a few thousand calls; manual)
uv run python -m pipeline actions           # rebuild the corporate action dataset
uv run python -m pipeline analytics         # compute the snapshot, print it, publish nothing
uv run python -m pipeline publish           # compute + upsert into Supabase
uv run python -m pipeline eod               # the nightly chain (what CI runs)
uv run python -m pipeline strategy          # advance the paper book
uv run python -m pipeline verify RELIANCE   # audit one symbol end to end
uv run python -m pipeline audit             # scan every symbol for unapplied actions
uv run python -m pipeline summary           # what the lake currently holds

# Checks — the same three CI runs
uv run ruff check pipeline/
uv run mypy
uv run pytest tests/ -q

# Web
npm run dev                                 # Vite on 5173, proxying /api and /auth to 8000
npm run build                               # also typechecks server/ and api/
supabase db push                            # apply migrations
```

`--local DIR` and `--no-r2` mirror the object store to disk, so the one expensive
pass over NSE's archives can be captured once, inspected, and pushed to R2
afterwards with `python -m pipeline sync --local DIR`.

## 6. Invariants — do not break these silently

1. **Highs and lows are on a closing basis.** `distATH`, `dist52`, `isATH`,
   `is52` and the `newHighs`/`newLows` counters all come from closes, never from
   intraday highs. Mixing the bases reports a stock as below a high it set
   itself. Pinned by `tests/test_ath_basis.py`.
2. **Raw bars are never mutated.** Corporate-action adjustment happens at compute
   time, `adjusted = raw / k` for bars *before* the ex-date, `volume * k`.
   Factors compound.
3. **The unit of a corporate action is the event** — one symbol on one ex-date —
   not the filing. A bonus and a split effective the same day are two rows whose
   factors multiply. Pinned by `tests/test_corporate_actions.py`.
4. **Nothing but `supabase/migrations/` issues DDL.** Not the pipeline, not the
   server. A missing table must fail loudly rather than be created in one place.
5. **In `strategy_*` tables, dates are `TEXT` ('YYYY-MM-DD') and money is
   `DOUBLE PRECISION`.** `DATE` returns through node-pg as a JS `Date` and
   serialises with a time component; `NUMERIC` returns as a *string*; `REAL`
   (float4) carries ~7 significant digits and a rupee-scale book needs 9.
6. **`rules.py` and `book.py` are shared by the live path and the backtest.**
   Re-validating a parameter change must exercise exactly the code that produces
   tomorrow's orders. `tests/test_backtest_fidelity.py` holds the engine to the
   reference run — if those literals move, the traded strategy is no longer the
   validated one.
7. **pandas is not a runtime dependency.** The rolling windows are numpy;
   `tests/test_windows.py` pins them to the pandas semantics the strategy was
   validated against.
8. **`# noqa: BLE001` marks a deliberate broad catch.** Several modules swallow
   exceptions on purpose (a failed sector refresh must not sink the night's
   ingest) and each says why. Ruff's `BLE` rules are on so an *accidental* broad
   catch stands out — do not add one without the comment.
9. **`.ts` import specifiers in `server/src` and `api/` must stay.** They are
   what lets the backend run under Node's native type stripping with no build
   step. They cost `tsconfig.json` `compilerOptions` and a `includeFiles` entry
   in `vercel.json`; that trade was made deliberately (architecture.md §10.1).
10. **The UI uses hash routing** so it deploys as static files with no SPA
    fallback rewrite.

## 7. Traps that have already cost time

- **`PREVCLOSE` in the bhavcopy is not restated on an ex-date.** Any split
  detector built on "stored close vs official previous close" silently finds
  nothing. Verify against the close-to-close gap instead.
- **NSE re-keys a company's filings to its *current* symbol; the bars never
  move.** A rename therefore unhooks every past filing from the bars it
  re-bases. HEG became HEGAM on 2026-09-22 and its 2026-09-07 demerger arrived
  filed as HEGAM while the bars that fell said HEG, which failed the nightly
  audit on a cliff its own dataset explained. Where the action states a ratio
  the same miss is silent: it records `no_bars` and is never applied.
  `corporate_actions.resolve_symbol` resolves filings through ISIN; never add a
  symbol-keyed join to the actions dataset without it.
- **`R2_TOKEN_VALUE` alone cannot authenticate.** The S3 API needs the access
  key **pair** (`R2_KEY_ID` / `R2_SECRET_KEY`); the secret is the token's
  SHA-256, but the key id is the token's id and cannot be derived.
- **`www.nseindia.com` blocks datacenter IPs** far harder than the archive host.
  The corporate actions feed and the FII/DII scrape both live there and both
  degrade gracefully — cached years from R2, a logged failure — by design.
- **Supabase TLS from `pg`:** use `?uselibpqcompat=true&sslmode=require`. A bare
  `sslmode=require` is treated as `verify-full` in pg 8.22+ and fails Supabase's
  pooler chain with "self-signed certificate in certificate chain".
- **Vercel needs the transaction pooler (6543), not 5432**, and `DB_POOL_MAX` is
  deliberately 3 — concurrency comes from more instances, not a deeper pool.
- **A green Vercel deploy is not evidence the API works.** The static build can
  succeed while every function route returns `FUNCTION_INVOCATION_FAILED`.
- **Marketaux free tier is 100 requests/day**, shared instance-wide. News is
  cached in `news_articles` and topped up on read; there is no background job
  because there is no long-lived process to run one in.
- **Sector labels are opaque keys everywhere downstream.** A sector that arrives
  under two spellings simply appears twice, with its members, breadth and
  position cap split across the two. `ingest/taxonomy.py` owns the vocabulary;
  `tests/test_taxonomy.py` pins it.

## 8. Configuration

Read from real environment variables first, then repo-root `.env` / `.env.local`
— which is how CI and Vercel inject secrets. Both halves share exactly one key,
`SUPABASE_DB_URL`.

Pipeline: `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_KEY_ID`, `R2_SECRET_KEY`,
`SUPABASE_DB_URL`, `HISTORY_START`, `NSE_DELAY`.

Server: `SUPABASE_DB_URL`, `DB_POOL_MAX`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URL`, `APP_URL`, `ALLOWED_EMAILS`,
`ALLOW_ALL_SIGNUPS`, `PORT`, `DEV_LOGIN`, `MARKETAUX_API_KEY`,
`NEWS_REFRESH_MIN`, `NEWS_SYMBOLS_PER_REQ`, `NEWS_SYMBOL_SUFFIX`.

Full table with defaults and purpose: [docs/architecture.md §8](docs/architecture.md).
Access is **closed by default** — with no `ALLOWED_EMAILS` set, every sign-in is
rejected. `DEV_LOGIN` mints a session with no authentication and is local-only.

## 9. Working style in this repo

- **Comments say why, not what.** Most non-obvious code here carries the incident
  that produced it; when you change such code, update the reason with it.
- **Docs are versioned with the code.** If you change behaviour described in
  `docs/`, `pipeline/README.md` or this file, change the prose in the same
  commit. Where the code and a doc disagree, the code wins — and the doc is a bug.
- **Failures are graded.** Before adding a `try/except`, decide whether the thing
  being guarded is worth the night's run, and say so in the comment.
- Run `ruff`, `mypy` and `pytest` before claiming a pipeline change is done; CI
  runs exactly those three.
