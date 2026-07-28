# Pulse

Swing-trader terminal for NSE (India): market breadth, sector strength, highs/breakouts,
drawdown structure and watchlists — computed from official exchange data and gated behind
Zerodha Kite SSO.

## Layout

| Path | What it is |
| --- | --- |
| `src/` | React terminal UI (Breadth, Charts, Sectors, Highs, Drawdown, Watchlist tabs) |
| `server/` | Backend: NSE EOD ingestion, analytics engine, Kite Connect SSO, REST API — see [server/README.md](server/README.md) |

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

   First launch bootstraps ~270 sessions of NSE history in the background
   (bhavcopies, delivery data, index closes) and computes analytics; the UI
   shows progress meanwhile.

3. **Frontend** (port 5173, proxies `/api` and `/auth` to the backend):

   ```bash
   npm install
   npm run dev
   ```

Sign in with Zerodha at the gate. Kite access tokens expire daily, so expect one
login per trading day — the same token powers live index quotes during market hours.
If the backend is unreachable the UI falls back to clearly-badged demo data.

## Data pipeline (daily, IST)

```
08:30  reference sync (index constituents, sector mapping)
09:15  live index quote polling (Kite REST)
18:45  EOD chain: UDiFF bhavcopy → MTO delivery → index closes → FII/DII flows
19:30  analytics: corporate-action adjustment, EMAs, breadth counters,
       52w/ATH distances, Mansfield RS, sector composite scores
```

Corporate actions (splits/bonuses) are detected automatically from official
prev-close discontinuities and applied as retroactive adjustment factors — raw
bars are never mutated.

## Storage

SQLite by default (zero setup, `server/data/pulse.db`). Set
`DATABASE_URL=postgres://...` to run the identical schema on
PostgreSQL/TimescaleDB for production.
