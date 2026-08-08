# Pulse data pipeline

Batch pipeline that turns NSE's published EOD files into the daily "Pulse"
snapshot. It owns everything between the exchange and the app database.

```
NSE archives ──► Cloudflare R2 ──► DuckDB ──► Supabase ──► Vercel / Node API
  bhavcopy       Parquet lake      analytics   ~2.5k rows      the UI
  (per session)  (~8M bars)        (one pass)  (a few MB)
```

## Why three stores

| Store | Holds | Why not somewhere else |
| --- | --- | --- |
| **R2** | 19 years of daily bars as Parquet, plus the raw vendor files | ~8M rows is roughly 1.2 GB in Postgres — over Supabase's free tier. As zstd Parquet it is ~250 MB, and R2 charges no egress. |
| **DuckDB** | nothing — it is a library, not a server | Runs inside the job, scans the Parquet on R2, exits. Nothing to host or pay for. |
| **Supabase** | the latest computed state per symbol, plus user tables | This is the only thing in the request path, so it stays small and indexed. |

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
uv run python -m pipeline summary           # what the lake currently holds
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

> Note: a plausible-looking shortcut does **not** work here. The bhavcopy carries
> a `PREVCLOSE` column, but NSE does not restate it on an ex-date — on the
> RELIANCE bonus it reads the unadjusted 1645.40. Any detector built on
> "stored close vs official previous close" silently finds nothing.

Convention: `adjusted = raw / k` for bars *before* the ex-date, `volume * k`.
Factors compound, so a symbol with two 1:1 bonuses has k = 4 on its oldest bars.

Not adjusted: demergers and schemes of arrangement. They move the price for real,
but no ratio is derivable from the filing, so they are left alone rather than
guessed at.

## Configuration

Read from repo-root `.env` / `.env.local`; real environment variables win, which
is how CI injects secrets.

| Key | Purpose |
| --- | --- |
| `R2_ACCOUNT_ID` | Cloudflare account id |
| `R2_BUCKET_NAME` | bucket (default `pulse-terminal`) |
| `R2_KEY_ID` / `R2_SECRET_KEY` | R2 **S3 API** token pair — `R2_TOKEN_VALUE` alone will not authenticate |
| `SUPABASE_DB_URL` | Postgres URI (Session pooler) — needed only by `publish` |
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
