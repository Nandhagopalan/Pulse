# Pulse

Swing-trader terminal for NSE (India): market breadth, sector strength, highs/breakouts,
drawdown structure and watchlists — computed from official exchange data and gated behind
Zerodha Kite SSO.

## Layout

| Path | What it is |
| --- | --- |
| `src/` | React terminal UI (Breadth, Charts, Sectors, Highs, Drawdown, Watchlist tabs) |
| `server/` | Backend: Kite Connect SSO, REST API, live quotes — see [server/README.md](server/README.md) |
| `pipeline/` | Python batch pipeline: NSE → R2 Parquet lake → DuckDB analytics → Supabase — see [pipeline/README.md](pipeline/README.md) |

## Quick start

1. **Credentials** — put your Kite Connect app keys in `.env.local` at the repo root:

   ```
   ZERODHA_API_KEY="..."
   ZERODHA_API_SECRET="..."
   ```

   On [developers.kite.trade](https://developers.kite.trade) set the app's Redirect URL to
   `http://localhost:5173/auth/kite/callback`.

2. **Backend** (port 8000, needs Node ≥ 23):

   ```bash
   cd server
   npm install
   npm start
   ```

   First launch bootstraps ~2500 sessions (~10 years) of NSE history in the
   background (bhavcopies, delivery data, index closes) and computes analytics;
   the UI shows progress meanwhile. This takes a while on a cold database —
   set `BACKFILL_SESSIONS` lower for a faster start, at the cost of shallower
   all-time-high detection. Re-running the backfill with a larger value
   deepens existing history rather than re-fetching it.

3. **Frontend** (port 5173, proxies `/api` and `/auth` to the backend):

   ```bash
   npm install
   npm run dev
   ```

Sign in with Zerodha at the gate. Kite access tokens expire daily, so expect one
login per trading day — the same token powers live index quotes during market hours.
If the backend is unreachable the UI falls back to clearly-badged demo data.

## Architecture

```
NSE archives ──► Cloudflare R2 ──► DuckDB ──► Supabase ──► Node API ──► React UI
  bhavcopy       Parquet lake      analytics   ~2.5k rows    + Kite SSO
  (per session)  19 yrs, ~8M bars  (nightly)   (a few MB)    + live quotes
```

Storage is split because the two halves have opposite needs. Nineteen years of
daily bars is ~1.2 GB in Postgres — past Supabase's free tier — but ~250 MB as
compressed Parquet on R2, where egress is free. Only the *derived* state per
symbol reaches the database the UI queries, which keeps it small and indexed.

DuckDB in the middle is a library, not a service: it runs inside the nightly job,
scans the Parquet on R2, emits ~2,500 rows, and exits. Nothing to host.

The batch half lives in [pipeline/](pipeline/) and runs nightly on GitHub Actions
at 19:45 IST. The Node server no longer ingests when `SUPABASE_DB_URL` is set —
it serves the API, Kite SSO, and live index quotes during market hours.

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
