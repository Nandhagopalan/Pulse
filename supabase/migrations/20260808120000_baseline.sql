-- Baseline schema for the Supabase (Postgres) database.
--
-- This file is the single source of truth for what production holds. It
-- replaces two older declarations that had to be kept in sync by hand:
-- pipeline/schema.sql (deleted) and the CREATE TABLE IF NOT EXISTS blob that
-- server/src/db.ts used to execute on every connect. The server no longer
-- issues DDL against Postgres; its remaining schema literal builds the local
-- SQLite database for the zero-setup dev path only.
--
-- Apply with:  supabase db push
--
-- Scope: Supabase holds only what a request actually reads. The 8M daily bars
-- and the reference datasets stay on R2 as Parquet, and the self-ingest dev
-- path keeps a fuller SQLite schema of its own (daily_bars, instruments,
-- corporate_actions) that has no counterpart here.
--
-- Types mirror the SQLite declarations deliberately: server/src/db.ts writes
-- one dialect of SQL for both backends, so a type that reads back differently
-- would surface as a confusing runtime error rather than a clean failure.

-- ── Published by the nightly pipeline (pipeline/publish.py) ─────────────────

-- One row per session: breadth aggregates as a JSON payload.
CREATE TABLE IF NOT EXISTS breadth_daily (
  date TEXT PRIMARY KEY,
  data TEXT
);

CREATE TABLE IF NOT EXISTS sector_scores (
  date TEXT, sector TEXT, data TEXT,
  PRIMARY KEY (date, sector)
);

CREATE TABLE IF NOT EXISTS stock_metrics (
  date TEXT, symbol TEXT, data TEXT,
  PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS index_bars (
  index_name TEXT, date TEXT,
  open REAL, high REAL, low REAL, close REAL,
  PRIMARY KEY (index_name, date)
);

-- Split-adjusted candle cache for the chart endpoint.
--
-- The chart never asks for more than 500 bars, so caching that tail here keeps
-- /api/stocks/:sym/candles a single indexed row read. Serving it from the R2
-- Parquet instead would mean a DuckDB scan per chart open, and the deep history
-- that lives there is for batch analytics, not the request path.
CREATE TABLE IF NOT EXISTS stock_candles (
  symbol TEXT PRIMARY KEY,
  date TEXT,                 -- latest session included
  data TEXT,                 -- columnar JSON: {d:[],o:[],h:[],l:[],c:[],v:[]}
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS ingest_log (
  ts TEXT, job TEXT, date TEXT, status TEXT, detail TEXT
);

-- ── Written by the Node server ─────────────────────────────────────────────

-- FII/DII flows are not part of the lake: the pipeline never sees them, the
-- server scrapes them directly even in pipeline mode (scheduler.ts).
CREATE TABLE IF NOT EXISTS fii_dii (
  date TEXT, category TEXT,  -- 'FII' | 'DII'
  buy REAL, sell REAL, net REAL,
  PRIMARY KEY (date, category)
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,       -- provider:provider_user_id
  provider TEXT, name TEXT, email TEXT, avatar TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  sid TEXT PRIMARY KEY,
  user_id TEXT,
  created_at TEXT, expires_at TEXT
);

CREATE TABLE IF NOT EXISTS user_watchlist (
  user_id TEXT, symbol TEXT,
  added_at TEXT,
  PRIMARY KEY (user_id, symbol)
);

CREATE TABLE IF NOT EXISTS user_prefs (
  user_id TEXT PRIMARY KEY,
  data TEXT,                 -- JSON: capital, riskPct, maxPos
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS news_articles (
  id TEXT,                   -- marketaux uuid
  symbol TEXT,               -- our NSE symbol this article is tagged to
  title TEXT, description TEXT, url TEXT, source TEXT,
  image_url TEXT,
  sentiment REAL,            -- -1..1 (entity sentiment for this symbol), NULL if unknown
  published_at TEXT,         -- ISO
  fetched_at TEXT,           -- ISO — when we stored it
  PRIMARY KEY (id, symbol)   -- one article can be tagged to several watchlist symbols
);

-- ── Indexes ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_stock_metrics_date ON stock_metrics(date);
CREATE INDEX IF NOT EXISTS idx_sector_scores_date ON sector_scores(date);
CREATE INDEX IF NOT EXISTS idx_index_bars_name_date ON index_bars(index_name, date DESC);
CREATE INDEX IF NOT EXISTS idx_news_symbol_date ON news_articles(symbol, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_watchlist_user ON user_watchlist(user_id);
