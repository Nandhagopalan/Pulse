# Pulse — Plan to open the terminal to public users

Status: **planning only, nothing implemented.**
Written 2026-08-04. Measurements taken against the live SQLite DB on that date.

---

## 1. Why other accounts cannot log in today

Not a bug. The Kite Connect app registered on developers.kite.trade is a
personal/individual app, and Zerodha restricts those logins to the account
holding the subscription. Every other Kite account gets an error.

Kite login is currently doing two unrelated jobs:

1. **Identity** — who is this user (`auth.ts` → `users` / `sessions`)
2. **Market data access** — the service token used for live index quotes

Job 2 does not need to be per-user. Splitting these two is the core of this plan.

---

## 2. How little the app actually depends on Kite

Every Kite call site in the codebase:

| Call site | Purpose |
|---|---|
| `server/src/live.ts:68` `getQuotes()` | 6-index strip, intraday only |

That is the complete list. Everything else — the breadth engine, sector
scores, drawdowns, highs, all ~2,900 stocks — comes from **public NSE
archives** (`nsearchives.nseindia.com`, see `server/src/ingest/nse.ts:17`)
and needs no credentials at all.

**Kite is ~2% of the data surface and only for live intraday ticks.**

### Do not share your Kite credentials

Reusing one personal token for all users is the one option to avoid:

- Violates Kite Connect terms (licensed to one authenticated individual);
  Zerodha can revoke API access.
- All users share one rate limit.
- The token expires daily (~06:00 IST) and needs *your* interactive login,
  so the app breaks for everyone each morning.
- A Kite access token is **not** read-only market data — it grants access to
  holdings, positions and orders on your account.

---

## 3. Measured resource profile

Taken 2026-08-04 on the live dataset (274 sessions, 2,899 symbols).

| Job | CPU | Peak heap | Frequency |
|---|---|---|---|
| `/api/market/summary` | milliseconds | small | per request |
| EOD ingest (`runEodIngest`) | **1.1 s** | **30 MB** | 1× per weekday |
| Analytics (`runAnalytics`) | **2.7 s** | **511 MB** | 1× per weekday |

Total scheduled compute: **~4 s per weekday ≈ 1.5 minutes per month.**

### Storage

| Table | Size |
|---|---|
| `daily_bars` (696k rows) | 56.3 MB |
| `daily_bars` indexes | 36.6 MB |
| `stock_metrics` | 5.8 MB |
| `index_bars` (+ index) | 4.8 MB |
| everything else | < 1 MB |
| **Total** | **105 MB / 754k rows** |

Growth: ~2,899 rows per session ≈ 725k rows/year ≈ **+93 MB/year**.
Projection: ~198 MB after one more year, ~290 MB after two.

**Supabase free tier is 500 MB** → comfortable for ~2 years, then needs a
retention policy on `daily_bars` or a paid tier.

### The constraint that shapes the architecture

`runAnalytics()` peaks at **511 MB** because it loads a 300-session ×
2,899-symbol matrix into `Float64Array`s (`analytics/engine.ts:28`, `WINDOW = 300`).

- **Supabase Edge Functions (~256 MB, Deno): cannot run it.**
- Vercel Hobby functions: 10 s max duration — 2.7 s today, but it scales with
  the window and symbol count, so it will creep toward the ceiling.

This is a stateful batch job, not a request handler. It belongs on a worker.

---

## 4. Target architecture

```
Vercel          → React frontend + /api/market/summary (read-only)
Supabase        → Postgres + Google Auth (+ pg_cron trigger)
GitHub Actions  → nightly EOD ingest + analytics worker
```

Three services, all free tier, no always-on container.

### Why GitHub Actions for the worker

The nightly job is a scheduled batch task with no inbound traffic.

- Free: 2,000 min/month on private repos; this job uses ~2 min/month.
- Runners have **7 GB RAM** — the 511 MB peak is trivial, with headroom to grow.
- Built-in secrets management for `DATABASE_URL`.
- The repo is already on GitHub. No extra vendor, no cold starts, no scaling config.

Caveats:
- Scheduled runs can be **delayed 5–15 min** under load. Harmless here — the
  EOD chain already retries until the bhavcopy appears (`scheduler.ts:59`).
- GitHub disables schedules on repos with no activity for 60 days.

### Alternatives considered

| Option | Verdict |
|---|---|
| **Fly.io** | Best cloud-worker choice. `scale-to-zero` machines wake on an HTTP call, run ~4 s, sleep. Explicit memory sizing (512 MB/1 GB). **Use this if live index quotes are kept**, since those need an always-on process. |
| **Railway** | Good DX but bills usage-time on an always-on container; no true scale-to-zero. ~$5/mo for 1.5 min of work. |
| **Vercel Pro** ($20/mo) | 60 s function duration covers analytics. Simplest topology if you want one vendor. |
| **Supabase Edge Functions** | Ruled out: ~256 MB limit vs 511 MB peak. |
| **Run locally** | Free and already works. Fine to start; fragile as a product. |

### The live index strip — decide before launch

Under your own Kite token this is the licensing grey area from §2, **and** it
breaks every morning at token expiry without an interactive login.

- **Recommended for launch:** drop live quotes, show EOD closes with an
  "as of" stamp. Zero cost, zero licensing risk, adequate for swing trading.
- **Later:** a licensed feed (Kite Connect business tier, TrueData,
  Global Datafeed) if intraday proves necessary — then add Fly.io.

---

## 5. Staged implementation

### Stage 1 — Move to Supabase Postgres

Unblocks every later stage and is independent of the hosting decision.

- [ ] Add `DATABASE_URL` to `.env.local` (Supabase → Settings → Database →
      **Session pooler**, port 5432; `SUPABASE_PASSWORD` goes in the URL).
      `config.ts:41` already reads it and `db.ts:147` already branches on it.
- [ ] Add `pg` to `server/package.json` (`PgDb.connect()` imports it at
      `db.ts:120` but it is not currently a dependency).
- [ ] **Validate the DDL against real Postgres.** Reviewed by inspection and it
      looks compatible — `TEXT`/`REAL`/`INTEGER`, composite PKs and all
      `ON CONFLICT … DO UPDATE/NOTHING` upserts are valid PG. The only SQLite-ism
      is the `PRAGMA` inside `SqliteDb` (`db.ts:102`), which never runs on PG.
      *Not yet executed against a live PG instance — do this first.*
- [ ] Migrate 754k rows (`pg_dump`-style export or a batched copy script).
- [ ] Re-run `runAnalytics()` against Postgres and compare output to SQLite.
- [ ] Watch for `PgDb.translate()` (`db.ts:126`) — it rewrites `?` → `$n`
      naively; verify no query contains a literal `?` inside a string.

### Stage 2 — Google SSO via Supabase Auth

- [ ] Enable Google provider in Supabase Auth.
- [ ] Frontend: Supabase JS client for sign-in.
- [ ] Backend: verify the Supabase JWT using `SUPABASE_SECRET_KEY`; keep
      `users.id` as `google:<sub>` (fits the existing `provider:id` convention).
- [ ] Make **Kite login optional** — a "connect your broker" toggle, not the
      front door. Keep `serviceAccessToken()` only if live quotes are retained.
- [ ] Fix the typo in `.env.local`: `SUPABASE_PUBSLISHABLE_KEY` →
      `SUPABASE_PUBLISHABLE_KEY`.

### Stage 3 — Per-user data

Currently device-local and lost on browser change:

| Data | Today | Target |
|---|---|---|
| Watchlist | `localStorage` (`App.tsx:59`) | `user_watchlist(user_id, symbol)` |
| Profile | `localStorage` (`profile.ts:27`) | `user_profiles(user_id, …)` |
| Rules/quotes | `localStorage` (`App.tsx:79`) | `user_rules(user_id, text)` |
| `watched_symbols` | **global table** | per-user, or keep global as a news-fetch union |

- [ ] Create the tables above, keyed by `user_id`.
- [ ] Enable **RLS** so users can only read their own rows.
- [ ] Market data tables stay shared and read-only to clients.
- [ ] One-time migration of existing `localStorage` values on first login.

### Stage 4 — Scheduling

- [ ] GitHub Actions workflow, `cron: '15 13 * * 1-5'` (18:45 IST), running
      EOD ingest + analytics against `DATABASE_URL`.
- [ ] Keep `jobs.ts` as the entry point — it already exposes
      `backfill` / `eod` / `analytics` subcommands.
- [ ] If an HTTP-triggered worker is used instead (Fly.io), protect the
      endpoint with a shared token so it is not publicly triggerable.
- [ ] Alert on failure (the workflow failing is a sufficient signal to start).

### Stage 5 — Hardening before public launch

- [ ] **CSRF protection** — cookie auth currently has none.
- [ ] **Rate limiting** — no endpoint has any.
- [ ] **Session pruning** — the `sessions` table grows forever; nothing deletes
      expired rows.
- [ ] Marketaux free tier is **100 req/day shared across all users** — will not
      survive public traffic. Needs per-user quota or a paid tier.
- [ ] Retention policy on `daily_bars` before the 500 MB ceiling.
- [ ] Free-tier caveat: **Supabase pauses projects after 7 days of inactivity**
      — disruptive for a daily cron. Micro plan (~$10/mo) avoids it.
- [ ] No automatic backups on free tier — schedule a periodic `pg_dump`.

### Stage 6 — Legal/product, before public launch

- [ ] Review NSE data redistribution terms — separate from and independent of
      the Kite licensing question in §2.
- [ ] Terms of service + privacy policy (storing Google identities).
- [ ] Prominent **"not investment advice"** disclaimer.

---

## 6. Open decisions

1. **Live index quotes: keep or drop for launch?** Drop → GitHub Actions is
   sufficient. Keep → need Fly.io *and* a resolution to the Kite licensing and
   daily-token-expiry problems.
2. **Worker host:** GitHub Actions (recommended) vs Fly.io vs Vercel Pro.
3. **Supabase free vs Micro** — the 7-day inactivity pause is the deciding factor.
4. **Analytics memory:** optionally refactor `engine.ts` to stream per-symbol
   instead of loading the full matrix. Would drop the 511 MB peak enough for
   serverless and remove the worker-host question entirely. Not required for
   any option above.

---

## 7. Suggested order

Stage 1 → 2 → 3 are the real work and must be sequential.
Stage 4 can be done any time after Stage 1.
Stage 5 and 6 gate public launch, not development.

Start with **Stage 1** — self-contained, reversible, and required under every
option in §4.
