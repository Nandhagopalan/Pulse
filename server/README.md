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
- **Corporate actions**: detected from official prev-close discontinuities and
  stored as factors; prices are adjusted retroactively at compute/serve time
  (adjusted = raw / k), so raw ingested bars are never mutated.
- **Scaling path**: swap `DATABASE_URL` to TimescaleDB, move `ingest/` and
  `analytics/` into separate workers, put Redis behind `live.ts` — module
  boundaries already match those seams.
