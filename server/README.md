# Pulse backend

TypeScript backend for the Pulse swing-trader terminal: Google SSO and a read-only
REST API over the tables the pipeline publishes. Runs directly on Node ≥ 23 (native
type stripping) — no build step.

This server does not ingest anything. Market data is produced by the Python
pipeline in [../pipeline](../pipeline) and lands in Supabase; the API serves it.
There is no broker integration and no intraday path — every price served here is
an end-of-day close.

## Run

The database is Postgres, always — there is no local-file fallback, and the server
refuses to start without a connection string. For local development that means the
Supabase stack in Docker:

```bash
supabase start     # from the repo root; prints the local DB URI (port 54322)
```

Put the URI it prints in `.env.local` as `SUPABASE_DB_URL`, then:

```bash
cd server
npm install
npm start          # http://localhost:8000
```

`supabase start` applies everything in [../supabase/migrations](../supabase/migrations),
so a local database is built from exactly the same two files as production. An empty
one serves 503s from `/api/market/summary` until a session is published — run
`uv run python -m pipeline eod` to fill it, or point at the real Supabase instead.

## Configuration (.env / .env.local at repo root)

| Key | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | Google OAuth client credentials |
| `GOOGLE_REDIRECT_URL` | `http://localhost:5173/auth/google/callback` | Must exactly match an Authorized redirect URI on the OAuth client |
| `ALLOWED_EMAILS` | — | Comma-separated access list. **Closed by default**: with none set, every sign-in is rejected |
| `ALLOW_ALL_SIGNUPS` | off | `1` lets any Google account in, ignoring `ALLOWED_EMAILS` |
| `APP_URL` | `http://localhost:5173` | Where the browser lands after login |
| `PORT` | `8000` | API port |
| `SUPABASE_DB_URL` | — | **Required.** Postgres URI. `DATABASE_URL` is accepted as an alias |
| `DEV_LOGIN` | off | `1` enables `GET /auth/dev-login` (local session without Google — dev only) |
| `MARKETAUX_API_KEY` | — | Watchlist news. Unset disables the News tab |
| `NEWS_REFRESH_MIN` | `30` | A symbol refreshed this recently is skipped on read; keep ≥ 30 on the free tier |
| `NEWS_SYMBOLS_PER_REQ` | `5` | Symbols per Marketaux request, and the ceiling on one view's refresh — a page view costs at most one request |

## Architecture

```
  Google OAuth (SSO)              GitHub Actions, nightly 19:45 IST
        │                                      │
        ▼                                      ▼
  src/auth.ts / src/google.ts          pipeline/ → R2 → DuckDB
        │                                      │
        └──────────►  Supabase Postgres  ◄─────┘
                      (src/db.ts, schema from supabase/migrations)
                             │
                             ▼
  src/api.ts   /api/market/summary · /api/stocks/:sym/candles · /api/status
               /api/watchlist · /api/prefs · /api/news   (all per-user)
```

- **No schema here.** `src/db.ts` issues no DDL: [../supabase/migrations](../supabase/migrations)
  is the single source of truth, applied by `supabase db push` or the GitHub
  integration. The server assumes the tables exist and fails loudly if they do not.
- **No scheduler.** Nothing runs on a timer in this process; it is a pure request
  handler, which is what makes the Vercel port viable. The nightly work belongs to
  GitHub Actions.
- **Auth**: all `/api/*` routes require a session cookie issued by the Google SSO
  callback. Sessions live in the DB and last 30 days; they carry identity only, no
  third-party token. CSRF on the OAuth round trip is covered by a one-time `state`
  value held in a short-lived cookie and compared in constant time.
- **Per-user state**: `user_watchlist` and `user_prefs` are keyed by user id.
  `/api/news` reads the account's watchlist rather than trusting a query string,
  so a client cannot spend the shared Marketaux budget on symbols nobody watches,
  and it refreshes at most one batch per view — the news cache is warmed by reads
  now, not by a job.
- **Corporate actions**: adjustment factors are produced by the pipeline from
  NSE's corporate actions feed and verified against the tape; prices are adjusted
  at compute time (adjusted = raw / k), so raw bars are never mutated. The server
  reads the already-adjusted `stock_candles` cache and does no adjustment itself.
