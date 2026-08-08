-- Drop what the request path stopped reading when the R2 lake landed.
--
-- Every table below was still being created (server/src/db.ts ran its schema
-- blob on connect) and three of them were still being written every night by
-- pipeline/publish.py — but nothing in pipeline mode ever read them back. The
-- writers are removed in the same change as this migration.
--
-- All four are reconstructible from R2 if a reader ever needs them again, so
-- this loses no source data.

-- Superseded by stock_candles: analytics.py applies the adjustment inside
-- DuckDB and publishes the finished tail, so the raw actions were only ever
-- read by the Node-side adjustment chain that runs in self-ingest mode.
DROP TABLE IF EXISTS corporate_actions;

-- The 20-year bar history lives on R2 as Parquet. In pipeline mode nothing
-- populated this here, and /api/status already special-cases its absence.
DROP TABLE IF EXISTS daily_bars;

-- Write-only in every mode: published nightly, never queried. The screener
-- takes its sector from the stock_metrics payload, which analytics.py derives
-- from the constituents Parquet directly.
DROP TABLE IF EXISTS index_membership;
DROP TABLE IF EXISTS instruments;

-- Both are interest ledgers for a background news refresh that no longer
-- exists: with no long-lived process, /api/news tops up its own cache on read,
-- oldest symbol first, and user_watchlist already says who wants what.
-- watched_symbols was the single-user predecessor of news_interest.
DROP TABLE IF EXISTS news_interest;
DROP TABLE IF EXISTS watched_symbols;
