# Pulse data pipeline

Batch pipeline that turns NSE's published EOD files into the daily "Pulse"
snapshot. It owns everything between the exchange and the app database.

```
NSE archives ──► Cloudflare R2 ──► DuckDB ──► Supabase ──► Vercel / Node API
  bhavcopy       Parquet lake      analytics   ~2.5k rows      the UI
  (per session)  (~8M bars)        (one pass)  (a few MB)
```

## Layout

Packages follow the direction data flows, and the dependency graph is acyclic in
that same order — nothing in `sources/` knows about `ingest/`, nothing in
`ingest/` knows about `compute/`.

```
pipeline/
├── __main__.py            `python -m pipeline` → cli.main()
├── cli.py                 argument parsing and command dispatch
├── config.py              env resolution, R2 key layout       (depends on nothing)
├── sources/               the outside world
│   ├── nse.py             exchange HTTP: archives, bhavcopy, cross-check feeds
│   └── r2.py              object store (boto3) + DuckDB handle wired to it
├── ingest/                outside world → curated Parquet, all idempotent
│   ├── backfill.py        session bars, resumable, 2007 → today
│   ├── reference.py       index constituents and the sector map
│   ├── corporate_actions.py  splits and bonuses → adjustment factors
│   └── fno.py             index futures + options → the separate F&O bucket
├── compute/               curated lake → the published snapshot
│   ├── analytics.py       one DuckDB pass → breadth, sectors, per-symbol metrics
│   └── publish.py         the only writer to Supabase
├── jobs.py                chains; `eod` is what the nightly Action runs
└── verify.py              audit one symbol end to end against a second NSE source
```

## Checks

```bash
uv sync --group dev     # ruff, mypy and stubs
uv run ruff check pipeline/
uv run mypy
```

Both run on every push and PR via
[`.github/workflows/checks.yml`](../.github/workflows/checks.yml). Ruff's `BLE`
rules are enabled deliberately: several modules catch broad exceptions on
purpose — a failed sector refresh should not sink the night's ingest — and each
carries a `# noqa: BLE001` saying why, so an *accidental* broad catch stands out.
`UP` (pyupgrade) is off; modernizing the ~91 `typing.Optional`/`List`
annotations to 3.12 syntax is a separate change from turning linting on.

## Why three stores

| Store | Holds | Why not somewhere else |
| --- | --- | --- |
| **R2** | 19 years of daily bars as Parquet, plus the raw vendor files | ~8M rows is roughly 1.2 GB in Postgres — over Supabase's free tier. As zstd Parquet it is ~250 MB, and R2 charges no egress. |
| **DuckDB** | nothing — it is a library, not a server | Runs inside the job, scans the Parquet on R2, exits. Nothing to host or pay for. |
| **Supabase** | the latest computed state per symbol, plus user tables | This is the only thing in the request path, so it stays small and indexed. |

Supabase holds only what a request reads — 19 tables, listed in
[`supabase/migrations`](../supabase/migrations), which is the single source of
truth for that schema. Every dataset on both sides of the line — R2 keys, Parquet
schemas, Postgres columns, and which code writes and reads each — is catalogued
in [docs/data-map.md](../docs/data-map.md). Apply pending migrations with:

```bash
supabase db push        # apply pending migrations
```

Neither this pipeline nor the Node server issues DDL any more. Reference data
the request path never queries (instruments, constituents, corporate actions)
stays on R2 and is joined in DuckDB at compute time.

## Commands

Dependencies are managed with [uv](https://docs.astral.sh/uv/) from the repo-root
`pyproject.toml`; `uv run` syncs them automatically, and `uv.lock` pins the exact
versions CI installs.

```bash
uv run python -m pipeline backfill          # 2007 → today, NSE archives → R2 (~1–3 h, resumable)
uv run python -m pipeline reference         # index constituents + sector map
uv run python -m pipeline actions           # rebuild the corporate action dataset
uv run python -m pipeline analytics         # compute the snapshot, print it, publish nothing
uv run python -m pipeline publish           # compute + upsert into Supabase
uv run python -m pipeline eod               # the nightly chain (what CI runs)
uv run python -m pipeline verify RELIANCE   # audit one symbol end to end
uv run python -m pipeline audit             # scan every symbol for unapplied actions
uv run python -m pipeline summary           # what the lake currently holds
uv run python -m pipeline fno --start 2011-01-01 --end 2022-12-31 --bucket pulse-terminal-fno
                                            # index F&O → the separate F&O bucket (resumable)
uv run python -m pipeline fno --summary --bucket pulse-terminal-fno   # what that bucket holds
```

The backfill is safe to interrupt and re-run: raw vendor files already in R2 are
never re-fetched, and a year whose Parquet exists is skipped. Sessions that fail
outright are listed at the end of each year — re-run with `--force` to retry
just those.

Flags `--local DIR` and `--no-r2` mirror the object store to disk, so the one
expensive pass over NSE's archives can be captured once, inspected, and pushed to
R2 afterwards with `python -m pipeline sync --local DIR`.

## Object layout in R2

```
raw/nse/bhavcopy/YYYY/YYYY-MM-DD.zip      vendor bytes, exactly as served
raw/nse/index_close/YYYY/YYYY-MM-DD.csv
raw/nse/corp_actions/YYYY.json
curated/daily/year=YYYY/data.parquet      normalized bars, one file per year
curated/index_daily/year=YYYY/data.parquet
curated/corporate_actions/actions.parquet
curated/instruments/constituents.parquet
```

Index futures and options live in a **separate bucket**, `FNO_R2_BUCKET`
(`pulse-terminal-fno`), with the same raw/curated split:

```
raw/nse/fo_bhavcopy/YYYY/YYYY-MM-DD.zip                 every F&O contract, vendor bytes
raw/nse/fovolt/YYYY/YYYY-MM-DD.csv                      clearing-house volatility file (carries spot)
raw/nse/fo_market_activity/YYYY/YYYY-MM-DD.zip         only for sessions NSE published no bhavcopy for
curated/index_fno_daily/year=YYYY/data.parquet          index futures + options, every strike and expiry
curated/index_underlying_daily/year=YYYY/data.parquet   one spot close per index per session
```

`fno` has no default bucket and refuses the terminal's, so this data cannot land
in `pulse-terminal` by accident. Units are as NSE publishes them in both layouts —
volume in contracts, open interest in units — and the legacy file's missing lot
size is recovered from notional turnover. Spot comes from FOVOLT, which carries
prices from late March 2011; sessions before that use the nearest future's close
and say so in `source`. Column-level detail is in
[docs/data-map.md §1.3](../docs/data-map.md).

The raw layer exists so every derived dataset can be rebuilt without asking NSE
again — their archives rate-limit hard, block datacenter IPs, and have retired
paths before. One file per year in `curated/` rather than one per session keeps
a full scan at ~19 range requests instead of ~4,700 round-trips.

## Corporate actions

Splits and bonuses re-base a price overnight. Uncorrected, RELIANCE looks like it
fell 50% on 2017-09-07 and has sat "90% below its all-time high" ever since —
which would poison every ATH, EMA and 52-week figure downstream.

The source of truth is NSE's corporate actions feed, which states the action
outright (`Bonus 1:1`, `Face Value Split ... From Rs 10/- To Rs 2/-`). Each parsed
ratio is then **verified against the tape**: the close-to-close gap at the ex-date
must agree with the stated ratio, or the event is recorded but not applied. That
catches mis-parsed labels, actions announced and never executed, and ex-dates
recorded a session off.

The unit is the **event** — one symbol on one ex-date — not the filing. A company
that declares a bonus and a split effective the same day files two rows, and the
two factors multiply, so filings are grouped and compounded before being verified.
AHCL's 2026-04-24 is `Bonus 1:1` next to a 10-to-2 split: a 10x re-basing that
neither row states on its own, and that verifying the rows one at a time rejects
twice over. The feed carries 33 of these over 19 years — BAJFINANCE, BAJAJFINSV,
NAZARA and SARVESHWAR among them — and not one is the re-filed duplicate that
grouping by symbol and ex-date might suggest.

Labels come in two house styles and both have to parse. Modern rows spell the
action out; rows from roughly 2007-2016 are abbreviated to fit a fixed-width
field — `Bon-1:1`, `Fv Splt Frm Rs 10 To Rs 2`, `Fv Spl-Rs10tore1/Bon-1:1` — which
is ~50 filings, concentrated in the years where a missed factor has had longest
to compound.

Three factor bands, because one pair cannot do three jobs: what a single filing
may state (a face value of Rs 100, a 22:1 bonus), what a compounded event may
reach (`SARVESHWAR` is k=30), and how wide the tape scan looks (an ETF unit split
re-bases ~100x). The narrowest band otherwise wins everywhere, and the evidence
for the largest events gets filtered out before it can be weighed.

> Note: a plausible-looking shortcut does **not** work here. The bhavcopy carries
> a `PREVCLOSE` column, but NSE does not restate it on an ex-date — on the
> RELIANCE bonus it reads the unadjusted 1645.40. Any detector built on
> "stored close vs official previous close" silently finds nothing.

Convention: `adjusted = raw / k` for bars *before* the ex-date, `volume * k`.
Factors compound, so a symbol with two 1:1 bonuses has k = 4 on its oldest bars.

Not adjusted: demergers and schemes of arrangement. They move the price for real,
but no ratio is derivable from the filing, so they are left alone rather than
guessed at.

## Auditing

`corporate_actions` checks its **inputs**: does the ratio NSE stated match what
the tape did? A disagreement is answered by applying nothing, which is the safe
default and a completely silent one. It also cannot see an action that was never
filed, never parsed, or split across two filings that each look wrong alone.

`python -m pipeline audit` asks the opposite question, of the **output**: does the
adjusted history still contain cliffs that nothing explains? A close that falls
44% in one session and stays there has had a split or bonus go unapplied,
whatever the feed says. One scan grades the whole universe:

Every cliff is explained from data wherever that is possible, and only what
survives is reported:

| verdict | count | meaning |
| --- | --- | --- |
| **scheme** / **rights** | 46 | The feed states a demerger, scheme of arrangement or discounted rights issue. `corporate_actions` records these — factor 1.0, `status = not_adjusting` — precisely so the audit can point at them. |
| **fund unit** | 50 | ETF and mutual-fund units (ISIN `INF…`). NSE's action feed is an *equity* feed and carries no unit splits, so GOLDBEES' ~100x has no filing to find. |
| *(dropped)* | — | Round trips, both legs — a move the next fortnight undoes is a trade, not a re-basing — and anything under ₹1, where one tick is 100%. |
| **accepted** | 7 | What survives all of that, reviewed by hand in [`accepted_residuals.json`](accepted_residuals.json). |
| **new** | 0 | The alarm. Fails the run. |

Deriving those first buckets rather than listing them is not tidiness. The list
came first, and it was **actively dangerous**: eleven of its seventy-eight rows
were real unapplied splits — KCP's 10-to-1, GREAVESCOT's 5-to-1, MANAPPURAM's 1:1
bonus — mistaken for demergers and suppressed in perpetuity by the very file
meant to reduce noise. A rule cannot make that mistake, because it has to point
at the filing that explains the cliff. Chasing those eleven down found the parser
bug behind them: `_SPLIT_RE` scanned from the start of the label, so
`1st Interim Dividend Rs.2.50 … Face Value Split From Rs.10/- To Re.1/-` read the
*dividend* as the face value and made a 10-to-1 split look like 2.5.

Steady state is 103 residuals and exit 0. Anything new raises, and because the
audit is the last step of `eod` and runs *after* publish, the snapshot is already
in Supabase before the failure: nobody loses data, and the failed scheduled
workflow is the one notification channel this repo has. An outage is not a
finding — an unreachable lake logs and returns rather than crying wolf, because
mail that fires on infrastructure gets filtered, and then the next real finding
goes unread.

```bash
uv run python -m pipeline audit                   # report, exit 1 on anything new
uv run python -m pipeline audit --warn            # report, always exit 0
uv run python -m pipeline audit --accept-current  # rewrite the baseline (review the diff!)
```

## Configuration

Read from repo-root `.env` / `.env.local`; real environment variables win, which
is how CI injects secrets.

| Key | Purpose |
| --- | --- |
| `R2_ACCOUNT_ID` | Cloudflare account id |
| `R2_BUCKET_NAME` | bucket (default `pulse-terminal`) |
| `R2_KEY_ID` / `R2_SECRET_KEY` | R2 **S3 API** token pair — `R2_TOKEN_VALUE` alone will not authenticate |
| `SUPABASE_DB_URL` | Postgres URI (Session pooler) — needed only by `publish` |
| `FNO_R2_BUCKET` | bucket for index F&O data (`pulse-terminal-fno`); no default, never the terminal's bucket — needed only by `fno`, or pass `--bucket` |
| `HISTORY_START` | backfill floor, default `2007-01-01` |
| `NSE_DELAY` | seconds between archive requests, default `0.15` |

## Scheduling

`.github/workflows/eod.yml` runs `python -m pipeline eod` at 14:15 UTC (19:45 IST)
on weekdays, an hour after NSE publishes. Every step is idempotent, so a re-run
converges rather than double-counting and a missed day is fixed by running it
again.

The one thing to watch on the first CI run: `www.nseindia.com` (the corporate
actions feed) blocks datacenter IPs more aggressively than the archive host. The
job degrades gracefully — cached action years are served from R2 — but if it
fails there persistently, run `python -m pipeline actions` from a residential IP
periodically and let CI use the cache.
