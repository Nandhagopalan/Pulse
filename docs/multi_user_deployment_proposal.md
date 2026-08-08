# Proposal: Multi-User Deployment on Vercel

**Status:** Accepted with amendments — stage 1 in progress (see §7)
**Date:** 2026-08-08
**Supersedes:** the deployment assumptions in [pulse_architecture_design.md](pulse_architecture_design.md)
(that doc assumes Next.js App Router; what actually got built is Vite + React with a
standalone long-lived Node server in `server/`)

---

## 1. The proposal

Turn Pulse from a single-operator tool into something other people can sign into,
hosted on Vercel.

1. **Deploy to Vercel** — frontend and API.
2. **Identity is not the broker.** Users sign in with a general SSO (Google via
   Supabase Auth) rather than Zerodha Kite.
3. **The broker link is optional and per-user.** After signing in, a user may
   attach their own broker API credentials from inside the terminal UI. Nothing
   broker-related is baked into the repo or shared env.
4. **Broker credentials are stored server-side**, encrypted, never returned to
   the browser.
5. **The broker is only ever used for live quotes** — index strip and individual
   stocks during market hours. It is not on the path for anything else.
6. **No broker linked → live is simply off.** Those users see the same
   NSE-archive-derived data everyone else sees.

## 2. Assessment

The identity/broker split is the right call and should happen regardless of
hosting. The Vercel half is a genuine rewrite of the server's execution model,
not a config change. Three things in the proposal need resolving before work
starts (§4), and three prerequisites are missing from it entirely (§5).

### 2.1 Why the split is right

- `auth.ts:69` mints users as `kite:<id>`. Today a person needs a Zerodha
  account merely to view EOD data that came free from public NSE archives.
- `SESSION_TTL_H = 24` (`auth.ts:19`) exists *only* because Kite tokens die
  daily. Once identity is separate, sessions can last 30 days while the broker
  token expires on its own schedule.
- Supabase is already a dependency (`SUPABASE_DB_URL`), so Supabase Auth is the
  cheapest identity provider to add. `users` / `sessions` (`db.ts:66-74`)
  survive largely intact, re-keyed by provider.
- `api.ts:50-55` already overlays the live price only when a quote exists, so
  the no-broker degradation path is half-built.

### 2.2 The most important thing this fixes

`serviceAccessToken()` (`auth.ts:52-55`) is documented as *"most recent Kite
access token across all logins."* One user's credential currently fetches the
quotes served to every other user. Acceptable for a single-operator tool,
indefensible with two users. The per-user model deletes it. **This alone
justifies the change.**

### 2.3 Where the Kite token is actually used (full trace)

Two jobs, and nothing else.

**Job 1 — identity.** `LoginGate.tsx:22` → `/auth/kite/login` (`auth.ts:58`) →
`kite.zerodha.com` → callback (`auth.ts:62`) → `exchangeToken()` (`kite.ts:58`)
→ writes a `users` row, a `sessions` row, and the global
`meta['kite_access_token']` (`auth.ts:76`).

**Job 2 — six index quotes.** `scheduler.ts:40` (20s tick) →
`pollIndexQuotes()` (`live.ts:59`) → `serviceAccessToken()` → `getQuotes()`
for the six keys hardcoded at `live.ts:10-17` (NIFTY 50, BANK, MIDCAP 100,
SMLCAP 100, IT, INDIA VIX) → in-memory `Map`. Consumed in exactly one place,
`api.ts:50-55`, where it overwrites three fields of the index strip: `value`,
`chgPct`, and one appended sparkline point.

**Everything else comes from the NSE-archive pipeline** — stock prices,
breadth, sector scores, ATH/52w, candles, FII/DII flows, the instrument
master. No stock-level quote is ever live: during market hours individual
stocks already show the previous close, and only the six index tiles update.

**Dead on arrival:** `getProfile` (`kite.ts:68`), `getInstrumentsCsv`
(`kite.ts:73` — the `auth.ts:8` comment claiming the token is kept for
"instrument sync" is stale; `ingest/nse.ts` uses NSE files),
`getDailyCandles` (`kite.ts:88`, needs the paid historical add-on),
`liveAuthBlocked()` (`live.ts:45`), the `kiteConnected` field returned by
`/auth/me`, and the `kite_token` column in both schemas — none are ever called
or read.

**Consequence for sequencing:** removing Kite quotes is a small, self-contained
change that also deletes most of §3's serverless blockers (the poller, the
in-memory cache, the `rejectedToken` state). It is worth doing *before* the
Vercel port rather than porting code that is about to be deleted. It does not
remove `scheduler.ts` entirely — the news refresh also rides the same tick
(`scheduler.ts:42-49`).

## 3. What Vercel breaks

The server is a long-lived process with in-memory state. Almost every piece of
it is incompatible with per-request functions.

| Component | Location | Why it breaks |
| --- | --- | --- |
| `startScheduler()` 20s tick | `server/src/scheduler.ts:113-116` | No always-on process. Vercel Cron is 1/min max (Pro), daily on Hobby |
| Live quote `Map` cache | `server/src/live.ts:21` | Module state dies per invocation; instances don't share it |
| `rejectedToken` auth backoff | `server/src/live.ts:30` | Same — no shared memory to park in |
| SQLite fallback | `server/src/db.ts:151-154` | Read-only filesystem. Must hard-fail without `SUPABASE_DB_URL` |
| `.env.local` disk read | `server/src/config.ts:31` | File absent in prod; degrades quietly into the SQLite trap above |
| `new pg.Pool(...)` | `server/src/db.ts:120` | A pool per invocation exhausts Supabase; needs the transaction pooler (port 6543) |
| `bootCatchup()` | `server/src/index.ts:26` | There is no boot |

**The saving grace:** per-user tokens and serverless want the same
architecture. With no shared service token there is nothing left to poll
*with*, so the 20s poller was already dead on arrival. `scheduler.ts` and
`live.ts` get deleted from the serving path rather than ported.

**Replacement for live quotes:** pull-on-demand. A request arrives; if that
user has a broker token, fetch the quotes inline and cache the response in a
Postgres row for ~15s. Cost scales with active users instead of running
continuously against one person's credential.

**Unchanged:** EOD ingest and analytics stay on GitHub Actions
(`.github/workflows/eod.yml`, nightly 19:45 IST). That half is already
deployment-agnostic and needs no work.

### 3.1 Kite redirect URL constraint

Kite Connect permits **one** redirect URL per app. Vercel preview deployments
get random URLs, so login will only ever work on the production domain or a
fixed alias.

```
KITE_REDIRECT_URL = https://<domain>/auth/kite/callback
APP_URL           = https://<domain>
```

Both must also be registered on developers.kite.trade.

## 4. Open questions to settle first

### 4.1 "Never stored" vs "stored server-side"

The proposal says both. Kite forces the answer: `api_secret` signs the SHA-256
checksum during token exchange (`server/src/kite.ts`), and Kite Connect is a
server-to-server API — browser calls to `api.kite.trade` do not receive CORS
headers. **The credential must live server-side. There is no client-only
variant.**

Workable reading of the original intent — "not in the repo, not in shared env,
not shared between users":

- User pastes their key into the terminal UI, never into a file.
- Encrypted at rest (AES-256-GCM, key in Vercel env).
- Never returned to the client; `/auth/me` exposes only `brokerConnected: bool`.
- Working disconnect that deletes the row.

### 4.2 A Kite access token can place orders

It is not a read-only quote credential. Storing other users' tokens makes Pulse
a custodian of trade-capable secrets. Minimum obligations:

- Explicit consent screen at link time stating what the token can do.
- Token never written to logs or error messages.
- Functioning revoke/delete.
- A stated, and enforced, promise that Pulse calls quote endpoints only.
- Read Zerodha's Connect terms on who may use an app's key before opening this
  to anyone outside a personal circle.

### 4.3 Kite Connect costs ~₹2,000/month per app

*(verify current pricing)*

"Bring your own API key" means each user needs their own paid subscription —
realistically almost nobody will. This decides whether live data is a core
feature or a rarity:

- **Option A** — Pulse keeps paying for its own Connect app; users log in
  through it. Live data works for everyone. Cost is yours, and per-app rate
  limits (quote endpoint ~3 req/s) are shared across all users.
- **Option B** — BYO key only. Zero cost, near-zero adoption of live data.
- **Option C** *(leaning)* — Option A as the default provider, BYO key as a
  power-user path that sidesteps the shared rate limit.

## 5. Prerequisites the proposal omits

1. ~~**The displayed fundamentals are fabricated.**~~ **Resolved — removed.**
   `src/lib/fundamentals.ts` generated P/E, ROE, D/E, promoter holding and
   FII/DII holding from a seeded RNG (`mulberryRng(strHash(sym))`) over
   per-sector typical profiles, and rendered them as fact in the watchlist
   stat row, the sortable table columns, and the stock drawer. Harmless
   placeholder in a single-operator tool; dangerous once strangers size
   positions on it. The file and every consumer are gone — see stage 4 below.

2. **Personal details are the shipped defaults.** `src/lib/profile.ts:13-21`
   hardcodes a name, handle, email and ₹10L capital figure. Every new user
   would see the operator's details prefilled. Must come from the SSO profile.

3. **`watched_symbols` is genuinely shared across users** (`db.ts:90-93`).
   `registerWatched()` (`news.ts:107`) writes every requesting user's symbols
   into one global table and `refreshWatchlistNews()` (`news.ts:144`) refreshes
   the union — so a user's news feed is driven by strangers' stars.
   *Note:* the watchlist itself is **not** leaking. It lives in `localStorage`
   (`App.tsx:55-72`) and is already per-browser. Moving it server-side is for
   cross-device persistence and to let the server scope news per user — a
   feature, not a leak fix.

4. **Marketaux is a shared 100 req/day tier.** Per-user watchlists multiply the
   symbol union, and the refresh walks all of it every 30 minutes. Roughly ten
   users with distinct watchlists exhaust the quota. Needs a per-user budget,
   fair scheduling, or news as opt-in.

5. **"Live off → fetch like other stocks" yields yesterday, not delayed
   today.** The pipeline is EOD-only. There is no free real-time NSE feed that
   can legally be redistributed, and NSE blocks datacenter IPs — which is all
   Vercel has. The honest non-broker experience during market hours is *last
   close, badged as such*, not a delayed tick.

6. **No per-user data model exists** beyond `users` / `sessions`. Watchlists,
   profile, and any future broker links all need tables and row-level scoping.

## 6. Target architecture

Reflects the §7 amendment: no broker, no live quotes, one data source.

```
                    Google OAuth (SSO)
                            |
                            v
  React (Vite, static on Vercel)  ──►  /api/* Vercel Functions
                                            |
                                            v
                                   Supabase Postgres
                                   - analytics cache (shared)
                                   - users / sessions
                                   - per-user watchlist + profile
                                            ^
                                            |
     GitHub Actions EOD ──► R2 Parquet lake ──► DuckDB ──► Supabase
                    (unchanged, nightly 19:45 IST)
```

Every price in the product is then the previous session's close, uniformly, for
every user — which is what stocks already do today. Stage 4 of §7 makes the UI
say so.

A broker link, if it ever returns, slots in as an additive per-user credential
feeding a short-lived quote cache; §4 records what that would require.

## 7. Sequencing (agreed 2026-08-08)

The optional broker link of §1.3 is **deferred**. Given §2.3 — the token buys
six index tiles and nothing else — the decision was to remove Kite outright and
treat any future broker link as a purely additive feature, not a migration.
That collapses §4.1 and §4.2 into non-problems for now: no third-party
credential is stored at all.

| # | Stage | Status | Why here |
| --- | --- | --- | --- |
| 1 | Google SSO replaces Kite SSO. Profile seeded from the SSO identity; hardcoded personal defaults retired. | **done** | Kite cannot be removed until another way in exists. |
| 3 | Remove Kite entirely — `kite.ts`, `live.ts`, the quote overlay, the SSO routes, `ZERODHA_*` config, the `sessions.access_token` column and the 24h TTL. | **done** | Pulled forward with stage 1: Kite login was the only thing still using it. |
| 2 | Per-user watchlist table; scope `watched_symbols` by user. | **done** | Needs real user IDs from stage 1. |
| 4 | Fundamentals decision (§5.1) — remove, badge, or source real data. | **done** (removed) | Hard gate on letting strangers in. |
| 5 | Vercel port: `/api/*` as Functions, transaction pooler, hard-fail without `SUPABASE_DB_URL`, no `.env.local` disk read, no `bootCatchup()`. | pending | Much smaller now that stage 3 has removed the poller and the in-memory cache. |
| 6 | News quota strategy — per-user budget, fair scheduling, or opt-in. | pending | Only bites at multi-user scale. |

### Stage 1 + 3, as built

- Google OAuth 2.0 authorization-code flow in `server/src/google.ts`, zero
  dependencies, matching the plain-HTTPS style of the client it replaced.
  **Chose direct Google OAuth over Supabase Auth**: for a single provider it is
  comparable code, avoids a second SDK, and keeps one user table instead of
  reconciling ours with `auth.users`. Swappable later if more providers appear.
- CSRF via a one-time `state` in a 10-minute HttpOnly cookie, compared with
  `timingSafeEqual`. Unverified Google emails are rejected.
- Sessions carry identity only, 30 days, no third-party token anywhere.
- Access control is closed by default (`ALLOWED_EMAILS`), openable with
  `ALLOW_ALL_SIGNUPS=1`. See §8.
- `isMarketOpen()` moved to `util.ts` and is now only a status label; the
  scheduler tick dropped from 20s to 60s with nothing left to poll.

### Stage 2, as built

- `user_watchlist` and `user_prefs`, both keyed by user id, behind
  `/api/watchlist` (GET/POST/DELETE + `/import`) and `/api/prefs` (GET/POST).
- `watched_symbols` replaced by `news_interest (user_id, symbol, last_seen)`.
  **The old table is orphaned, not dropped** — `CREATE TABLE IF NOT EXISTS`
  cannot remove it and running DDL destructively on every boot is worse than a
  dead table. It is pure derived cache; drop it by hand when convenient.
- `/api/news` no longer accepts a `symbols` query parameter. It reads the
  account's watchlist server-side, so a client cannot direct the shared
  Marketaux budget at symbols nobody is watching.
- `NEWS_MAX_SYMBOLS_PER_REFRESH` (default 100) caps the cross-user union per
  refresh pass. This is a blunt instrument and does not make §6 unnecessary —
  it only stops one pass from spending the whole daily budget.
- Watchlist writes are optimistic in the UI and reconciled against the server's
  reply; a rejected write reverts.
- Both localStorage keys (`pulse-watchlist`, and the prefs blob) migrate onto
  the account on first authenticated load, then get cleared so the browser copy
  cannot drift from the server's.
- Preference writes are debounced 600ms and clamped server-side to known
  numeric fields — the blob is echoed back to clients, so it must never become
  arbitrary storage.

### Stage 4, as built

Chose **removal** over badging: people skim badges, and a trader glancing at
"Avg ROE 18%" anchors on the number regardless of the label. Nothing real was
lost — none of these figures were ever sourced.

Deleted `src/lib/fundamentals.ts` and every consumer:

| Where | What went |
| --- | --- |
| `StockDrawer.tsx` | The whole Fundamentals section — 13 rows plus the STRONG/DECENT/WEAK "quality" score — and the now-unused `Row` helper |
| `StockTable.tsx` | The P/E and ROE columns, their sort specs, and two grid tracks (10 columns → 8) |
| `WatchTab.tsx` | "Avg ROE" and the average-P/E subtitle, replaced with **Avg from ATH** and a count at 52w/all-time highs — both real pipeline fields |
| `HighsTab.tsx`, `DrawdownTab.tsx`, `WatchTab.tsx` | Footnotes promising "click a row for fundamentals" |

**Still synthetic, but explicitly badged** and deliberately left alone: the
stock drawer's 60-session sparkline and the ChartsTab fallback series both come
from `ohlc()` in `lib/candles.ts` when real candles have not loaded, and label
themselves "sample". The demo dataset behind `buildData()` is likewise badged.
Worth revisiting, but a different decision from this one.

## 8. Decisions still needed

- [ ] **§5.1 fundamentals** — remove the panels, badge them as synthetic, or
      source real data. Recommendation: remove; badging synthetic financials
      still invites misreading.
- [ ] **Access control** — any Google account, or an allowlist? Determines how
      much the §5.4 quota and abuse questions matter.
      *Interim:* implemented as an env-gated allowlist, default closed, so the
      decision stays reversible with one variable.
- [ ] Production domain, so the OAuth redirect URL can be registered
- [ ] Whether Vercel Pro is in scope (Hobby cron is daily-only; matters only if
      anything scheduled moves off GitHub Actions)

### Deferred with the broker link

- [ ] §4.3 — who pays for Kite Connect, if live quotes ever come back
- [ ] Whether other brokers (Upstox, Dhan, Fyers) are in scope for a future
      `broker_links` schema, or Kite-only with room to grow
