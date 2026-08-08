# Pulse backend

TypeScript backend for the Pulse swing-trader terminal, implementing the NSE Market
Data Sourcing Plan: official-exchange EOD ingestion, a vectorized analytics engine,
and Zerodha Kite Connect SSO. Runs directly on Node ≥ 23 (native type stripping) —
no build step.

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
| `ZERODHA_API_KEY` / `ZERODHA_API_SECRET` | — | Kite Connect app credentials (SSO + quotes) |
| `KITE_REDIRECT_URL` | `http://localhost:5173/auth/kite/callback` | Must exactly match the Redirect URL registered at developers.kite.trade |
| `APP_URL` | `http://localhost:5173` | Where the browser lands after login |
| `PORT` | `8000` | API port |
| `DATABASE_URL` | *(unset → SQLite at `server/data/pulse.db`)* | Set `postgres://...` to run on PostgreSQL/TimescaleDB |
| `SUPABASE_DB_URL` | — | Supabase Postgres URI. Setting it turns on **pipeline mode** (below) |
| `PIPELINE_MODE` | `1` when `SUPABASE_DB_URL` is set | `0` forces the legacy self-ingesting behaviour |
| `BACKFILL_SESSIONS` | `270` | Trading sessions of history to bootstrap |
| `DEV_LOGIN` | off | `1` enables `GET /auth/dev-login` (local session without Kite — dev only) |

## Architecture

```
NSE archives (UDiFF bhavcopy, MTO, index closes, constituents)   Kite Connect (SSO, quotes)
        │                                                              │
        ▼                                                              ▼
  src/ingest/nse.ts  ──────────────►  storage (src/db.ts)  ◄────  src/auth.ts / src/kite.ts
        │                    SQLite by default, Postgres via DATABASE_URL
        ▼
  src/analytics/engine.ts   corporate-action factor chains, EMAs, breadth,
        │                   52w/ATH, Mansfield RS, sector composite scores
        ▼
  src/api.ts   /api/market/summary · /api/stocks/:sym/candles · /api/status
  src/scheduler.ts   IST schedule: 08:30 reference sync · market-hours quote
                     polling · 18:45 EOD chain → analytics · boot catch-up
```

- **Auth**: all `/api/*` routes require a session cookie issued by the Kite SSO
  callback. Sessions live in the DB; the freshest Kite access token is reused by
  backend jobs (Kite tokens expire daily).
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

Live index quotes, Kite SSO, watchlist news and the FII/DII fetch stay here in
both modes — none of them are part of the lake.
