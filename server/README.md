# Pulse backend

TypeScript backend for the Pulse swing-trader terminal, implementing the NSE Market
Data Sourcing Plan: official-exchange EOD ingestion, a vectorized analytics engine,
and Google SSO. Runs directly on Node ≥ 23 (native type stripping) — no build step.

There is no broker integration and no intraday data path: every price served here is
an end-of-day close.

## Run

```bash
cd server
npm install
npm start          # http://localhost:8000
```

On first launch with an empty database the server bootstraps automatically:
downloads ~270 sessions of NSE bhavcopies + index closes in the background and
computes analytics (the frontend shows a progress screen meanwhile).

Manual jobs:

```bash
npm run backfill    # bootstrap history + analytics
npm run eod         # ingest latest session (bhavcopy, MTO, indices, FII/DII) + analytics
npm run analytics   # recompute analytics only
```

## Configuration (.env / .env.local at repo root)

| Key | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | Google OAuth client credentials |
| `GOOGLE_REDIRECT_URL` | `http://localhost:5173/auth/google/callback` | Must exactly match an Authorized redirect URI on the OAuth client |
| `ALLOWED_EMAILS` | — | Comma-separated access list. **Closed by default**: with none set, every sign-in is rejected |
| `ALLOW_ALL_SIGNUPS` | off | `1` lets any Google account in, ignoring `ALLOWED_EMAILS` |
| `APP_URL` | `http://localhost:5173` | Where the browser lands after login |
| `PORT` | `8000` | API port |
| `DATABASE_URL` | *(unset → SQLite at `server/data/pulse.db`)* | Set `postgres://...` to run on PostgreSQL/TimescaleDB |
| `SUPABASE_DB_URL` | — | Supabase Postgres URI. Setting it turns on **pipeline mode** (below) |
| `PIPELINE_MODE` | `1` when `SUPABASE_DB_URL` is set | `0` forces the legacy self-ingesting behaviour |
| `BACKFILL_SESSIONS` | `270` | Trading sessions of history to bootstrap |
| `DEV_LOGIN` | off | `1` enables `GET /auth/dev-login` (local session without Google — dev only) |
| `MARKETAUX_API_KEY` | — | Watchlist news. Unset disables the News tab |
| `NEWS_REFRESH_MIN` | `30` | Minutes between news refreshes; keep ≥ 30 on the free tier |
| `NEWS_SYMBOLS_PER_REQ` | `5` | Symbols batched into one Marketaux request |
| `NEWS_MAX_SYMBOLS_PER_REFRESH` | `100` | Ceiling on symbols refreshed per pass across **all** users — the free tier is 100 requests/day in total |

## Architecture

```
NSE archives (UDiFF bhavcopy, MTO, index closes, constituents)   Google OAuth (SSO)
        │                                                              │
        ▼                                                              ▼
  src/ingest/nse.ts  ──────────────►  storage (src/db.ts)  ◄────  src/auth.ts / src/google.ts
        │                    SQLite by default, Postgres via DATABASE_URL
        ▼
  src/analytics/engine.ts   corporate-action factor chains, EMAs, breadth,
        │                   52w/ATH, Mansfield RS, sector composite scores
        ▼
  src/api.ts   /api/market/summary · /api/stocks/:sym/candles · /api/status
               /api/watchlist · /api/prefs · /api/news   (all per-user)
  src/scheduler.ts   IST schedule: 08:30 reference sync · 18:45 EOD chain →
                     analytics · boot catch-up (nothing runs intraday)
```

- **Auth**: all `/api/*` routes require a session cookie issued by the Google SSO
  callback. Sessions live in the DB and last 30 days; they carry identity only, no
  third-party token. CSRF on the OAuth round trip is covered by a one-time `state`
  value held in a short-lived cookie and compared in constant time.
- **Per-user state**: `user_watchlist` and `user_prefs` are keyed by user id, and
  `news_interest` records who asked for which symbol. `/api/news` reads the
  account's watchlist rather than trusting a query string, so a client cannot
  spend the shared Marketaux budget on symbols nobody watches.
- **Corporate actions**: adjustment factors are produced by the pipeline from
  NSE's corporate actions feed and verified against the tape; prices are adjusted
  at compute time (adjusted = raw / k), so raw bars are never mutated.

## Pipeline mode

When `SUPABASE_DB_URL` is set, the Python pipeline in [../pipeline](../pipeline)
owns ingestion and analytics, and this server stops doing both — running two
ingest paths against one database would have them overwrite each other. What
changes:

| | self-ingest (legacy) | pipeline mode |
| --- | --- | --- |
| EOD ingest + analytics | this server, 18:45/19:30 IST | GitHub Actions, 19:45 IST |
| bar history | `daily_bars` in the local DB | Parquet on Cloudflare R2 |
| `/api/stocks/:sym/candles` | adjusts `daily_bars` on the fly | reads the pre-adjusted `stock_candles` cache |
| constituent sync | this server, Mondays | pipeline, every EOD run |

Google SSO, watchlist news and the FII/DII fetch stay here in both modes — none of
them are part of the lake.
