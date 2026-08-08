# Pulse — Architecture and Deployment

**Status:** current as of 2026-08-08
**Supersedes:** `pulse_architecture_design.md` and `multi_user_deployment_proposal.md`,
both folded into this file. Where they disagreed with the code, the code won.

This is one document with two halves. **Part I** describes what exists and why —
the shape the system actually has, with the decisions that produced it. **Part II**
describes what is still proposed: the Vercel port, the news quota, and the
questions that remain open.

---

# Part I — As built

## 1. What Pulse is

A swing-trader terminal for NSE (India): market breadth, sector strength,
highs/breakouts, drawdown structure and watchlists, computed from official
exchange archives and gated behind Google sign-in.

Every price in Pulse is an **end-of-day close**. There is no intraday feed, no
broker integration, and no live quote path — the numbers advance once a night
when the pipeline runs. This is a property of the design, not a gap in it (§13.2).

## 2. System map

```
  Google OAuth (SSO)
        │
        ▼
  React (Vite) ──► Node API ──► Supabase Postgres ◄── GitHub Actions, nightly 19:45 IST
   src/            server/       13 tables, a few MB        │
                                                            │
                          NSE archives ──► R2 Parquet lake ──► DuckDB ──► publish
                            bhavcopy        19 yrs, ~8M bars   (one pass)
```

Four components, three of which are in this repo:

| Path | What it is |
| --- | --- |
| `src/` | React terminal UI — Breadth, Charts, Sectors, Highs, Drawdown, Watchlist, News tabs |
| `server/` | Node backend: Google SSO + read-only REST API. No build step, no scheduler, no ingest |
| `api/` | Vercel adapter — thirty lines that hand requests to the same router |
| `pipeline/` | Python batch pipeline: NSE → R2 Parquet → DuckDB → Supabase |
| `supabase/migrations/` | The schema. Single source of truth; nothing else issues DDL |

## 3. Why storage is split three ways

| Store | Holds | Why not somewhere else |
| --- | --- | --- |
| **Cloudflare R2** | 19 years of daily bars as Parquet, plus the raw vendor files and reference data | ~8M rows is ~1.2 GB in Postgres — past Supabase's free tier. As zstd Parquet it is ~250 MB, and R2 charges no egress |
| **DuckDB** | nothing — it is a library, not a service | Runs inside the nightly job, scans the Parquet on R2, emits ~2,500 rows, exits. Nothing to host or pay for |
| **Supabase Postgres** | the latest computed state per symbol, plus all user state | The only store in the request path, so it stays small and indexed |

The rule that falls out of this: **only derived state reaches the database the UI
queries.** Reference data the request path never joins against (instruments, index
constituents, corporate actions) stays on R2 and is joined in DuckDB at compute
time.

## 4. The pipeline

Runs nightly on GitHub Actions (`.github/workflows/eod.yml`, 14:15 UTC = 19:45 IST,
weekdays), an hour after NSE publishes. Every step is idempotent: a re-run
converges rather than double-counting, and a missed day is fixed by running it again.

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

### 4.1 Object layout in R2

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
paths before. One file per **year** in `curated/` rather than one per session keeps
a full scan at ~19 range requests instead of ~4,700 round-trips.

### 4.2 Corporate actions

Splits and bonuses re-base a price overnight. Uncorrected, RELIANCE looks like it
fell 50% on 2017-09-07 and has sat "90% below its all-time high" ever since —
poisoning every ATH, EMA and 52-week figure downstream.

The source of truth is NSE's corporate actions feed, which states the action
outright (`Bonus 1:1`, `Face Value Split ... From Rs 10/- To Rs 2/-`). Each parsed
ratio is then **verified against the tape**: the close-to-close gap at the ex-date
must agree with the stated ratio, or the event is recorded but not applied. That
catches mis-parsed labels, actions announced but never executed, and ex-dates
recorded a session off.

> A plausible-looking shortcut does **not** work here. The bhavcopy carries a
> `PREVCLOSE` column, but NSE does not restate it on an ex-date — on the RELIANCE
> bonus it reads the unadjusted 1645.40. Any detector built on "stored close vs
> official previous close" silently finds nothing.

Convention: `adjusted = raw / k` for bars *before* the ex-date, `volume * k`.
Factors compound, so a symbol with two 1:1 bonuses has k = 4 on its oldest bars.
**Raw bars are never mutated** — adjustment happens at compute time.

Not adjusted: demergers and schemes of arrangement. They move the price for real,
but no ratio is derivable from the filing, so they are left alone rather than
guessed at.

## 5. Database schema

Thirteen tables, defined in [`supabase/migrations`](../supabase/migrations) and
nowhere else. Neither the server nor the pipeline issues DDL; `supabase db push`
applies them, and `supabase start` applies the same files to the local Docker
stack, so dev and production are built from identical SQL.

**Published by the pipeline** (`pipeline/publish.py`):

| Table | Shape | Notes |
| --- | --- | --- |
| `breadth_daily` | `date PK, data` | One row per session; breadth aggregates as a JSON payload |
| `sector_scores` | `(date, sector) PK, data` | |
| `stock_metrics` | `(date, symbol) PK, data` | The ~2,500-row core of a session |
| `index_bars` | `(index_name, date) PK, ohlc` | Feeds the index strip and index charts |
| `stock_candles` | `symbol PK, date, data, updated_at` | Split-adjusted tail, columnar JSON `{d,o,h,l,c,v}` |
| `fii_dii` | `(date, category) PK, buy/sell/net` | Scraped by `publish.py`, not from the lake |
| `meta` | `key PK, value` | `last_ingested_session`, `last_analytics_date` |
| `ingest_log` | `ts, job, date, status, detail` | Surfaced by `/api/status` |

`stock_candles` is a deliberate cache rather than a lake read: the chart never asks
for more than 500 bars, so caching that tail keeps `/api/stocks/:sym/candles` a
single indexed row read. Serving it from Parquet would mean a DuckDB scan per chart
open, and the deep history there is for batch analytics, not the request path.

**Written by the server**:

| Table | Shape | Notes |
| --- | --- | --- |
| `users` | `id PK (provider:sub), provider, name, email, avatar` | Keyed on the Google subject id, which is stable — emails get reassigned |
| `sessions` | `sid PK, user_id, created_at, expires_at` | 30-day TTL; identity only, no third-party token |
| `user_watchlist` | `(user_id, symbol) PK, added_at` | Capped at 200 symbols |
| `user_prefs` | `user_id PK, data, updated_at` | JSON: `capital`, `riskPct`, `maxPos` — clamped server-side |
| `news_articles` | `(id, symbol) PK, …` | Marketaux cache; one article can tag several symbols |

Indexes exist on `stock_metrics(date)`, `sector_scores(date)`,
`index_bars(index_name, date DESC)`, `news_articles(symbol, published_at DESC)`
and `user_watchlist(user_id)`.

## 6. The server

`server/` runs directly on Node ≥ 23 using native type stripping — no build step,
two runtime dependencies. It is a **pure request handler**: nothing in it runs on
a timer, and it never ingests. That property is what makes the Vercel port of
Part II a small job rather than a rewrite.

```
src/app.ts      the route table — shared by both entry points
src/index.ts    entry point 1: http.createServer, for local development
src/router.ts   zero-dependency router: params, cookies, JSON body cap (64 KB)
src/auth.ts     Google SSO routes, session issue/resolve, allowlist
src/google.ts   OAuth 2.0 authorization-code client, plain HTTPS, no SDK
src/api.ts      the terminal API — every route behind requireAuth
src/news.ts     Marketaux fetch + cache, refreshed on read
src/db.ts       Postgres adapter (`?` placeholders translated to `$n`)
src/config.ts   env, with .env/.env.local fallback for local development
```

| Route | Purpose |
| --- | --- |
| `GET /auth/google/login` · `/auth/google/callback` | SSO round trip |
| `GET /auth/me` · `POST /auth/logout` | Session identity |
| `GET /api/market/summary` | The whole dashboard payload for the latest session |
| `GET /api/stocks/:sym/candles?n=` | Adjusted candles, ≤ 500; indices resolve from `index_bars` |
| `GET/POST/DELETE /api/watchlist[/:sym]` · `POST /api/watchlist/import` | Per-user watchlist |
| `GET/POST /api/prefs` | Per-user preferences |
| `GET /api/news` | Watchlist news, symbols read server-side |
| `GET /api/status` | Coverage, last session, recent ingest log |
| `GET /health` | Unauthenticated liveness |

### 6.1 Auth model

Google OAuth 2.0 authorization-code flow, confidential client — the secret stays
server-side, so no PKCE. CSRF is covered by a one-time `state` in a 10-minute
HttpOnly cookie, compared with `timingSafeEqual`. Unverified Google emails are
rejected outright, since the allowlist is keyed on the email.

Identity comes from the userinfo endpoint rather than by decoding the `id_token`:
the code is exchanged over TLS directly with Google, so one extra round trip buys
the same guarantee without hand-rolling JWT verification.

Access control is **closed by default**. `ALLOWED_EMAILS` is the access list and an
address not on it is bounced at the gate; `ALLOW_ALL_SIGNUPS=1` opens it to any
Google account. A deployed instance is therefore never accidentally open to
everyone.

### 6.2 Per-user scoping

`/api/news` reads the account's watchlist server-side and does **not** accept a
`symbols` query parameter — a client must not be able to direct the shared
Marketaux budget at symbols nobody is watching. Watchlist writes are optimistic in
the UI and reconciled against the server's reply; a rejected write reverts. Both
legacy `localStorage` keys migrate onto the account on first authenticated load and
are then cleared, so the browser copy cannot drift from the server's.

Preferences are debounced 600 ms client-side and clamped server-side to known
numeric fields — that blob is echoed back to clients, so it must never become a
place to park arbitrary content.

### 6.3 News

Marketaux free tier is 100 requests/day. Symbols are batched several per request
(`NEWS_SYMBOLS_PER_REQ`, default 5) and everything is cached in `news_articles`.
The API serves from that cache and tops it up **on read** when it has gone stale —
there is no background job, because there is no long-lived process to run one in.
A page view spends at most one Marketaux request, so a large watchlist degrades to
slower rotation rather than sudden exhaustion. §11 covers what this still does not
solve.

## 7. The frontend

Vite + React 19, no UI framework, no state library. `src/lib/api.ts` is the entire
network surface; `vite.config.ts` proxies `/api` and `/auth` to port 8000 in
development. If the backend is unreachable the UI falls back to clearly-badged
demo data.

Still synthetic and **explicitly badged**: the stock drawer's 60-session sparkline
and the ChartsTab fallback series, both from `ohlc()` in `lib/candles.ts` when real
candles have not loaded, and the demo dataset behind `buildData()`. Worth
revisiting; not the same decision as §13.3.

## 8. Configuration

Read from real environment variables first, then repo-root `.env` / `.env.local` —
which is how CI and any hosted deployment inject secrets.

| Key | Used by | Default | Purpose |
| --- | --- | --- | --- |
| `SUPABASE_DB_URL` (alias `DATABASE_URL`) | server, pipeline | — | **Required.** Postgres URI. The server refuses to start without it |
| `DB_POOL_MAX` | server | `3` | Connections per instance. Small on purpose (§9.3) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | server | — | OAuth client credentials |
| `GOOGLE_REDIRECT_URL` | server | `http://localhost:5173/auth/google/callback` | Must exactly match an Authorized redirect URI on the OAuth client |
| `APP_URL` | server | `http://localhost:5173` | Where the browser lands after login |
| `ALLOWED_EMAILS` | server | — | Comma-separated access list. Closed by default: with none set, every sign-in is rejected |
| `ALLOW_ALL_SIGNUPS` | server | off | `1` lets any Google account in |
| `PORT` | server | `8000` | API port (ignored on serverless) |
| `DEV_LOGIN` | server | off | `1` enables `GET /auth/dev-login`. **Local only** |
| `MARKETAUX_API_KEY` (alias `MARKET_AUX_KEY`) | server | — | Watchlist news; unset disables the News tab |
| `NEWS_REFRESH_MIN` | server | `30` | A symbol refreshed this recently is skipped on read; keep ≥ 30 on the free tier |
| `NEWS_SYMBOLS_PER_REQ` | server | `5` | Symbols per Marketaux request, and the ceiling on one view's refresh |
| `NEWS_SYMBOL_SUFFIX` | server | `.NS` | RELIANCE → RELIANCE.NS |
| `R2_ACCOUNT_ID` | pipeline | — | Cloudflare account id |
| `R2_BUCKET_NAME` | pipeline | `pulse-terminal` | Bucket |
| `R2_KEY_ID` / `R2_SECRET_KEY` | pipeline | — | R2 **S3 API** token pair — `R2_TOKEN_VALUE` alone will not authenticate |
| `HISTORY_START` | pipeline | `2007-01-01` | Backfill floor |
| `NSE_DELAY` | pipeline | `0.15` | Seconds between archive requests |

The `R2_*` keys are needed only by the pipeline, and `SUPABASE_DB_URL` is the only
key both halves share.

---

# Part II — Proposed

## 9. Stage 5 — the Vercel port

**Status:** **done and live** at `https://pulse-woad-eta-30.vercel.app`, deployed
from `main` through the GitHub integration. §10.1 records what the first deploys
got wrong and why.

### 9.1 What used to block it, and why it no longer does

The original assessment listed seven incompatibilities between the server and
per-request functions. Six were deleted rather than ported, because removing the
broker link (§13.1) removed the only thing they served:

| Blocker | Resolution |
| --- | --- |
| `startScheduler()` 20 s tick | **Deleted.** With no shared service token there was nothing left to poll |
| Live quote `Map` cache | **Deleted** with the quote overlay |
| `rejectedToken` auth backoff | **Deleted** with the Kite client |
| SQLite fallback | **Deleted.** `db.ts` is Postgres-only and hard-fails without a URL |
| `bootCatchup()` on boot | **Deleted.** The pipeline owns ingest |
| `.env.local` disk read | Harmless: absent in prod, `parseEnvFile` returns `{}`, real env wins. It no longer degrades into anything, because there is nothing left to degrade into |
| `new pg.Pool(...)` per invocation | **Still real.** See §9.3 |

What remains is a hosting change, not a rewrite.

### 9.2 Shape of the port, as built

The router already takes `(IncomingMessage, ServerResponse)`, which is exactly what
a Vercel Node function receives. So the whole backend is one function that hands
off to the existing `dispatch` — an adapter, not a second server.

```
server/src/app.ts    buildRouter(): the route table, shared by both entry points
api/pulse.ts         builds the router once per instance; (req, res) => dispatch
vercel.json          rewrites /api/*, /auth/* and /health to that function
```

Mechanical changes that came with it:

1. **`pg` moved to the root `package.json`**, because Vercel builds functions
   against the root manifest, not `server/package.json`. (`fflate` was no longer
   imported anywhere and was dropped.)
2. **Node pinned to 24** via `engines.node`, matching `.nvmrc`. Native TS type
   stripping and the `.ts` import specifiers in `server/src` both depend on it.
3. **`tsconfig.node.json` now includes `server/src` and `api`**, so `npm run build`
   typechecks the backend. It previously covered `vite.config.ts` alone — the
   server was never typechecked by any build.
4. **Build output** is the existing `npm run build` → `dist/`. Unchanged.

**No SPA fallback rewrite is needed.** The UI uses hash routing
(`src/lib/router.ts`) specifically so it deploys as plain static files, so every
real path is either a static asset or one of the three rewritten prefixes.

#### The rewrite/path subtlety

A Vercel rewrite hands the function its *destination* URL, so the path the client
asked for has to survive the hop. `vercel.json` passes it explicitly as
`__path=/auth/$1`, and `api/pulse.ts` restores `req.url` from it before routing.
If a given Vercel runtime preserves the original URL instead, `__path` is simply
absent and `req.url` is already correct. Both branches route correctly, which is
why it is written that way rather than depending on which behaviour applies.

### 9.3 The one real constraint: connection pooling

A `pg.Pool` per function instance will exhaust Supabase's connection limit under
any concurrency. The deployment must use Supabase's **transaction pooler**
(pgBouncer, port 6543), not the direct 5432 connection string. `node-postgres`
uses the extended query protocol without named prepared statements, so transaction
pooling is safe for every query the server issues.

The pool itself is now capped small — `DB_POOL_MAX`, default 3, with a 10-second
idle timeout — because concurrency here is meant to come from more instances, not
from a deep pool inside one. A default pool of 10 times a few dozen warm functions
exhausts Supabase long before the traffic justifies it.

**The TLS parameter is not optional and not obvious.** `pg` 8.22 treats a bare
`sslmode=require` as `verify-full`, and Supabase's pooler chain fails that with
*"self-signed certificate in certificate chain"*. Use
`?uselibpqcompat=true&sslmode=require`, which selects libpq semantics — encrypted,
without chain verification — and is the spelling that stays correct in `pg` 9.
`sslmode=no-verify` also works and means the same thing today, but the compat form
says so explicitly. Verifying the chain properly would mean shipping Supabase's CA
and using `verify-full`; worth doing if this ever carries more than EOD prices.

**Region.** The database is in `ap-northeast-2` (Seoul) and functions currently
execute in `bom1` (Mumbai). `buildSummary()` issues about ten sequential queries,
so every millisecond of cross-region latency is paid ten times per dashboard load.
Co-locating the function region with the database is the single cheapest
improvement available; parallelising the six index queries with `Promise.all` is
the next.

The pipeline is the opposite case — long single-writer jobs — and should keep the
session pooler string it already uses in CI.

### 9.4 Environment variables to set on Vercel

Everything the server reads, since `.env.local` does not exist there. Nothing R2
belongs here: the pipeline runs on GitHub Actions, not on Vercel.

| Key | Value |
| --- | --- |
| `SUPABASE_DB_URL` | Supabase **transaction pooler** URI (port 6543), with `?uselibpqcompat=true&sslmode=require` — see §9.3 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | From the OAuth client |
| `GOOGLE_REDIRECT_URL` | `https://<domain>/auth/google/callback` |
| `APP_URL` | `https://<domain>` |
| `ALLOWED_EMAILS` | The access list |
| `MARKETAUX_API_KEY` | Optional; unset disables the News tab |

Two that must **not** be set: `DEV_LOGIN` (it mints a session with no
authentication) and `ALLOW_ALL_SIGNUPS`, unless §14 is decided the other way.

Note that linking the Supabase integration to Vercel does not by itself satisfy
`SUPABASE_DB_URL` — the integration injects its own variable names, and it injects
the direct connection string rather than the pooled one. Either set
`SUPABASE_DB_URL` explicitly, or teach `config.ts` to accept the integration's
name as a further alias. Setting it explicitly is preferable, because it is also
where the 6543 choice of §9.3 gets made.

### 9.5 Domain and OAuth

Google permits several redirect URIs per client, but each must be registered
literally. Vercel preview deployments get a fresh URL per commit, so **sign-in will
only work on the production domain or a fixed alias** unless every preview URL is
registered, which is not practical. Previews will render the UI and fail at the
gate; that is acceptable, but it should be a known property rather than a surprise.

Both `GOOGLE_REDIRECT_URL` and `APP_URL` must point at that stable domain, and the
redirect URI must be registered on the OAuth client before the first deploy.

### 9.6 Cookie hardening — done

`setCookie` emitted `HttpOnly; SameSite=Lax` with no `Secure`. It now adds
`Secure` whenever `APP_URL` is an `https://` URL. That derivation beats a
`NODE_ENV` guess: the deployment that needs the flag is exactly the one served
over HTTPS, and localhost — where a Secure cookie would never be stored at all —
keeps working untouched.

### 9.7 What stays where it is

EOD ingest and analytics remain on GitHub Actions. That half is already
deployment-agnostic and needs no work, and moving it would mean paying for Vercel
Pro to get sub-daily cron (§14).

## 10. Deployment checklist

Code (done):

- [x] `pg` at the root `package.json`; `engines.node` pinned to 24
- [x] `api/pulse.ts` and `vercel.json` per §9.2
- [x] `Secure` on session cookies over HTTPS (§9.6)
- [x] Pool caps for a many-instance runtime (§9.3)

Operations (remaining, in order):

1. Pick the production domain and register
   `https://<domain>/auth/google/callback` on the Google OAuth client.
2. Set the §9.4 variables on the Vercel project — production, and preview too if
   previews should at least render before failing at the gate.
3. Confirm `supabase db push` has applied both migrations to the hosted project.
4. Confirm the nightly Action has published at least one session. An empty
   database serves a clean 503 from `/api/market/summary`, not a crash, so this
   is verifiable either way.
5. Deploy, then verify in this order: `GET /health` (no auth, proves the function
   and its rewrite work), Google sign-in (proves the redirect URI and the
   allowlist), `GET /api/status` (proves the database connection and shows what
   the pipeline has published).

### 10.1 What the first deploy taught us

The `.ts` import specifiers were the flagged risk, and they did bite — twice, in
two different toolchains, with the same misleading symptom both times: **the
static build succeeds, the site serves, and every function route returns
`FUNCTION_INVOCATION_FAILED`.** A green deployment is not evidence the API works.

1. **Type-check (`TS5097`).** `@vercel/node` type-checks `api/pulse.ts` against
   the repo-root `tsconfig.json`, which is solution-style — `files: []` plus two
   references, no `compilerOptions` — so the check ran on compiler defaults and
   rejected every `import './config.ts'`. Fixed by giving that file
   `compilerOptions` (`allowImportingTsExtensions`, `noEmit`, nodenext). They are
   inert locally: with `files: []` they compile nothing, and `tsc -b` builds the
   referenced projects with their own settings.

2. **Dependency tracing.** With the types accepted, the function still failed at
   module load: the tracer does not follow `.ts` specifiers *out of* `api/`, so
   `server/src` was never copied into the lambda. Fixed with
   `includeFiles: "server/src/**"` in vercel.json.

Rewriting the imports to drop `.ts` would have solved both, and is the wrong
trade: those specifiers are exactly what lets `server/` run under Node's native
type stripping with no build step.

**Runtime logs are not reachable with a project-scoped token** — the CLI insists
on resolving a user, and `/v1/deployments/{id}/runtime-logs` 404s. Build logs come
from `/v3/deployments/{id}/events?builds=1`. Diagnosing the second failure needed
a temporary lazy import with a try/catch that reported the load error over HTTP;
worth remembering as the technique if a future deploy fails opaquely.

## 11. Stage 6 — news quota

**Status:** pending. Only bites at multi-user scale.

Marketaux free tier is 100 requests/day, shared across the whole instance.
Per-user watchlists multiply the symbol union, and roughly ten users with distinct
watchlists exhaust the quota. `NEWS_SYMBOLS_PER_REQ` bounds what a single page view
can spend, which stops one pass from burning the day's budget, but it does not
allocate fairly between users.

Three options, undecided: a per-user daily budget, fair scheduling across the
union, or making news opt-in per account.

---

# Part III — Record

## 12. Sequencing

| # | Stage | Status |
| --- | --- | --- |
| 1 | Google SSO replaces Kite SSO; profile seeded from the SSO identity | **done** |
| 3 | Remove Kite entirely — client, live quotes, SSO routes, `ZERODHA_*`, `sessions.access_token`, the 24 h TTL | **done** (pulled forward with stage 1) |
| 2 | Per-user watchlist and prefs tables; news scoped per user | **done** |
| 4 | Fundamentals decision | **done** — removed |
| — | Migrations become the only schema; self-ingest and the SQLite path deleted | **done** |
| 5 | Vercel port (§9) | **done** — live, deploying from `main` |
| 6 | News quota strategy (§11) | pending |

## 13. Decisions and why

### 13.1 The broker link was removed, not deferred in place

The Kite access token did exactly two jobs: identity, and six index quotes
refreshed on a 20-second tick. No stock-level quote was ever live — individual
stocks already showed the previous close during market hours.

Against that, three costs. `serviceAccessToken()` used *the most recent Kite token
across all logins* to fetch the quotes served to every user, which is defensible
for one operator and indefensible for two. A Kite access token can place orders, so
storing other users' tokens would make Pulse a custodian of trade-capable secrets.
And Kite Connect costs roughly ₹2,000/month per app, so "bring your own key" would
mean near-zero adoption.

Six index tiles did not justify any of that, so Kite was deleted outright and any
future broker link is treated as purely additive. This also removed most of the
serverless blockers in §9.1 — the poller, the in-memory cache, the backoff state.

### 13.2 One data source, uniformly

There is no free real-time NSE feed that can legally be redistributed, and NSE
blocks datacenter IPs — which is all a hosted deployment has. So "live off →
fetch like other stocks" would yield *yesterday*, not a delayed today. The honest
product is the previous session's close for every user, badged as such, which is
what the UI now says.

### 13.3 Fabricated fundamentals were removed, not badged

`src/lib/fundamentals.ts` generated P/E, ROE, D/E, promoter holding and FII/DII
holding from a seeded RNG over per-sector profiles, and rendered them as fact in
the watchlist stat row, the table columns and the stock drawer. Harmless in a
single-operator tool; dangerous once strangers size positions on it.

Removal beat badging because people skim badges — a trader glancing at "Avg ROE
18%" anchors on the number regardless of the label. The file and every consumer are
gone: the drawer's Fundamentals section and quality score, two table columns and
their sort specs, the WatchTab averages (replaced with **Avg from ATH** and a count
at 52w/all-time highs, both real pipeline fields), and three footnotes promising
fundamentals on click.

### 13.4 Direct Google OAuth over Supabase Auth

For a single provider it is comparable code, avoids a second SDK, and keeps one
`users` table instead of reconciling ours with `auth.users`. Swappable later if
more providers appear.

### 13.5 Migrations are the only schema

Two declarations previously had to be kept in sync by hand: `pipeline/schema.sql`
and a `CREATE TABLE IF NOT EXISTS` blob the server executed on every connect.
Both are gone. `supabase/migrations` is applied by `supabase db push` in production
and by `supabase start` locally, so both are built from identical SQL, and a
missing table now fails loudly instead of being silently created in one place.

### 13.6 An access allowlist, closed by default

Implemented as an env-gated list so the decision stays reversible with one
variable. Default closed means a deployed instance is never accidentally open to
every Google account on earth.

## 14. Open decisions

- [ ] **A custom domain**, if the generated `pulse-woad-eta-30.vercel.app` is not
      the long-term address. Changing it means updating `APP_URL` and
      `GOOGLE_REDIRECT_URL` and adding the new redirect URI in Google Console.
- [ ] **Access control at launch** — allowlist, or open signups? Determines how
      much §11 and abuse questions matter.
- [ ] **Vercel Pro in scope?** Hobby cron is daily-only. Matters only if anything
      scheduled ever moves off GitHub Actions (§9.7).
- [ ] **News quota strategy** (§11).

### Deferred with the broker link

If a broker link ever returns, it slots in as an additive per-user credential
feeding a short-lived quote cache. What that would require, recorded so it need not
be re-derived:

- The credential must live server-side — `api_secret` signs the token-exchange
  checksum, and `api.kite.trade` does not send CORS headers, so there is no
  client-only variant.
- Encrypted at rest, never returned to the browser (`/auth/me` exposes only
  `brokerConnected: bool`), with a working disconnect that deletes the row.
- An explicit consent screen stating that the token can place orders, a stated and
  enforced promise that only quote endpoints are called, and the token never
  written to logs or error messages.
- Quotes pulled on demand and cached in Postgres for ~15 s, so cost scales with
  active users rather than running continuously.
- Open: who pays for Kite Connect, and whether other brokers (Upstox, Dhan, Fyers)
  are in scope for a `broker_links` schema.
