# Pulse

Swing-trader terminal for NSE (India): market breadth, sector strength, highs/breakouts,
drawdown structure and watchlists — computed from official exchange data and gated behind
Google sign-in.

Every price in Pulse is an **end-of-day close**. There is no intraday feed and no broker
integration; the numbers advance once a night when the pipeline runs.

## Layout

| Path | What it is |
| --- | --- |
| `src/` | React terminal UI (Breadth, Charts, Sectors, Highs, Drawdown, Watchlist tabs) |
| `server/` | Backend: Google SSO + REST API — see [server/README.md](server/README.md) |
| `pipeline/` | Python batch pipeline: NSE → R2 Parquet lake → DuckDB analytics → Supabase — see [pipeline/README.md](pipeline/README.md) |
| `docs/` | [Multi-user deployment proposal](docs/multi_user_deployment_proposal.md) — the plan this is being built against |

## Quick start

1. **Credentials** — create an OAuth client at
   [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services →
   Credentials → *OAuth client ID* → Web application, and add
   `http://localhost:5173/auth/google/callback` as an Authorized redirect URI.
   Then put the keys in `.env.local` at the repo root:

   ```
   GOOGLE_CLIENT_ID="..."
   GOOGLE_CLIENT_SECRET="..."
   ALLOWED_EMAILS="you@example.com, teammate@example.com"
   ```

   `ALLOWED_EMAILS` is the access list, and it is **closed by default** — an address
   that is not on it gets bounced at the gate. Set `ALLOW_ALL_SIGNUPS=1` to let any
   Google account in. In a non-local deployment also set `GOOGLE_REDIRECT_URL` and
   `APP_URL` to the real domain, and register that redirect URI on the OAuth client.

2. **Database** — Postgres, always; the backend refuses to start without one.
   Locally that is the Supabase stack in Docker:

   ```bash
   supabase start
   ```

   It applies [`supabase/migrations`](supabase/migrations) — the single source of
   truth for the schema — and prints a connection URI. Put that in `.env.local`
   as `SUPABASE_DB_URL`, or point at the hosted project instead.

3. **Backend** (port 8000, needs Node ≥ 23):

   ```bash
   cd server
   npm install
   npm start
   ```

   The server only serves; it never ingests. A freshly created database is empty
   until the pipeline publishes a session into it — `uv run python -m pipeline eod`
   does that, or use the hosted Supabase, which the nightly Action already fills.

4. **Frontend** (port 5173, proxies `/api` and `/auth` to the backend):

   ```bash
   npm install
   npm run dev
   ```

Sign in with Google at the gate; sessions last 30 days. If the backend is unreachable
the UI falls back to clearly-badged demo data.

## Architecture

```
NSE archives ──► Cloudflare R2 ──► DuckDB ──► Supabase ──► Node API ──► React UI
  bhavcopy       Parquet lake      analytics   ~2.5k rows    + Google SSO
  (per session)  19 yrs, ~8M bars  (nightly)   (a few MB)
```

Storage is split because the two halves have opposite needs. Nineteen years of
daily bars is ~1.2 GB in Postgres — past Supabase's free tier — but ~250 MB as
compressed Parquet on R2, where egress is free. Only the *derived* state per
symbol reaches the database the UI queries, which keeps it small and indexed.

DuckDB in the middle is a library, not a service: it runs inside the nightly job,
scans the Parquet on R2, emits ~2,500 rows, and exits. Nothing to host.

The batch half lives in [pipeline/](pipeline/) and runs nightly on GitHub Actions
at 19:45 IST. The Node server does not ingest at all — it serves the API and
Google SSO, nothing more, and nothing in it runs on a timer.

The schema those two halves share lives in [supabase/migrations](supabase/migrations)
and nowhere else. Neither the server nor the pipeline issues DDL.

### Corporate actions

Splits and bonuses re-base prices overnight; left alone, RELIANCE reads as a 50%
crash on 2017-09-07. Ratios come from NSE's corporate actions feed and are each
verified against the actual close-to-close gap at the ex-date before being
applied, so a mis-parsed label or an announced-but-never-executed action is
recorded rather than silently distorting every ATH downstream. Raw bars are never
mutated — adjustment happens at compute time (`adjusted = raw / k`).

## Storage

Set `SUPABASE_DB_URL` (Postgres URI) and the pipeline's `R2_*` keys in
`.env.local`. Without them the server falls back to its legacy self-ingesting
mode on SQLite at `server/data/pulse.db`, which needs no setup but only carries
the history it downloads itself.
