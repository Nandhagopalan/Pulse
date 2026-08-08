-- Supabase (PostgreSQL) schema for the tables the pipeline writes.
--
-- Column types deliberately mirror server/src/db.ts so the Node server can run
-- against the same database unchanged: it issues CREATE TABLE IF NOT EXISTS for
-- this same set on connect, and a type mismatch there would surface as a
-- confusing runtime error rather than a clean failure.
--
-- What lives here is only the *derived* state — a few thousand rows. The 8M
-- daily bars stay on R2 as Parquet; they would not fit the free tier and nothing
-- in the request path needs them.

CREATE TABLE IF NOT EXISTS instruments (
  symbol TEXT PRIMARY KEY,
  name TEXT, isin TEXT, series TEXT,
  industry TEXT,
  sector TEXT,
  kite_token INTEGER,
  active INTEGER DEFAULT 1,
  listing_date TEXT
);

CREATE TABLE IF NOT EXISTS index_membership (
  index_name TEXT, symbol TEXT,
  PRIMARY KEY (index_name, symbol)
);

CREATE TABLE IF NOT EXISTS index_bars (
  index_name TEXT, date TEXT,
  open REAL, high REAL, low REAL, close REAL,
  PRIMARY KEY (index_name, date)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
  symbol TEXT, ex_date TEXT, kind TEXT,
  factor REAL,
  detail TEXT,
  PRIMARY KEY (symbol, ex_date, kind)
);

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

CREATE INDEX IF NOT EXISTS idx_stock_metrics_date ON stock_metrics(date);
CREATE INDEX IF NOT EXISTS idx_sector_scores_date ON sector_scores(date);
CREATE INDEX IF NOT EXISTS idx_index_bars_name_date ON index_bars(index_name, date DESC);
