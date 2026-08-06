# System Architecture Specification & Implementation Blueprint
**Project Name:** Pulse Swing Trading Terminal  
**Target Environment:** Vercel (Frontend & Edge APIs), Cloudflare R2 (Parquet Data Lakehouse), Supabase PostgreSQL (Analytics Cache & User State), DuckDB (Batch Analytics Engine)

---

## 1. Architectural Overview & System Flow

The system splits historical storage from real-time state querying:
* **Cloudflare R2:** Cold/Warm raw store for 20+ years of daily and intraday OHLCV Parquet files.
* **DuckDB Pipeline:** EOD batch computation engine (runs locally, via GitHub Actions, or on a Cloudflare Worker / Python container) to scan R2 Parquet files and generate daily metrics.
* **Supabase PostgreSQL:** Ultra-lean hot database that holds only the latest calculated state ("Pulse"), stock metadata, user watchlists, and alert configs.
* **Vercel (Next.js App Router):** High-speed dashboard interface querying Supabase directly via serverless APIs and Server Actions.

```
                  +-----------------------------------+
                  |   Daily Market EOD / Data Vendor  |
                  +-----------------+-----------------+
                                    |
                                    v
                  +-----------------------------------+
                  |  Daily Ingestion & Conversion     |
                  |  (Appends Parquet to Cloudflare R2)|
                  +-----------------+-----------------+
                                    |
                                    v
                  +-----------------------------------+
                  |  DuckDB Batch Execution Engine    |
                  |  - Reads R2 Parquet files via S3  |
                  |  - Calculates ATH, 20 EMA,        |
                  |    Breakout Scores, Volume Surges |
                  +-----------------+-----------------+
                                    |
                                    | UPSERT (Lightweight JSON / SQL)
                                    v
+------------------+      +-----------------------------------+
|  Vercel Frontend | <---> |  Supabase PostgreSQL DB           |
|  (Next.js App)   | REST/ |  - stock_analytics_pulse (<100MB) |
|  - Pulse UI      | WS    |  - stock_master                   |
|  - Screener      |       |  - user_watchlists / alerts       |
+------------------+      +-----------------------------------+
```

---

## 2. Cloudflare R2 Storage Layer (Data Lake)

### Directory Partitioning Structure
Store data in columnar **Apache Parquet** format, partitioned by timeframe and symbol:

```
r2://pulse-market-data/
├── daily/
│   └── symbol=NSE_RELIANCE/
│       └── data.parquet
│   └── symbol=NSE_TCS/
│       └── data.parquet
├── 1hour/
│   └── year=2026/
│       └── nse_1h_2026.parquet
└── master/
    └── stocks_master.json
```

### Parquet Schema Definition
Each Parquet file should strictly adhere to this schema:

| Column Name | Data Type | Compression | Description |
| :--- | :--- | :--- | :--- |
| `timestamp` | `TIMESTAMP_MS` | Snappy / ZSTD | Candle start time (UTC) |
| `symbol` | `STRING` | Dictionary | Standard symbol (e.g., `NSE:RELIANCE`) |
| `open` | `DOUBLE` | Plain | Opening price |
| `high` | `DOUBLE` | Plain | High price |
| `low` | `DOUBLE` | Plain | Low price |
| `close` | `DOUBLE` | Plain | Closing price |
| `volume` | `INT64` | Delta | Trading volume |

---

## 3. Analytics Engine (DuckDB & Daily EOD Pipeline)

The analytics worker executes after market close (e.g., 4:00 PM IST). It connects to Cloudflare R2 using DuckDB's HTTP/S3 filesystem plugin, processes full-history scans, and computes indicator thresholds.

### DuckDB Batch Query SQL (Example Script)
This query runs directly against R2 Parquet files to calculate All-Time Highs (ATH), 20 EMA, and Breakout candidates:

```sql
-- Load S3/R2 Credentials in DuckDB
INSTALL httpfs; LOAD httpfs;
SET s3_endpoint='<ACCOUNT_ID>.r2.cloudflarestorage.com';
SET s3_access_key_id='<R2_ACCESS_KEY>';
SET s3_secret_access_key='<R2_SECRET_KEY>';

-- Compute EOD Pulse Analytics across 20-year history
WITH history AS (
    SELECT 
        symbol,
        timestamp::DATE as trade_date,
        close,
        high,
        low,
        volume,
        MAX(high) OVER (
            PARTITION BY symbol 
            ORDER BY timestamp 
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) as prev_ath,
        MAX(high) OVER (PARTITION BY symbol) as all_time_high,
        AVG(close) OVER (
            PARTITION BY symbol 
            ORDER BY timestamp 
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) as ema_20 -- simplified sma/ema calculation
    FROM read_parquet('s3://pulse-market-data/daily/*/*.parquet')
),
latest_day AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
    FROM history
)
SELECT 
    symbol,
    trade_date,
    close as last_price,
    all_time_high,
    ROUND(((close - all_time_high) / all_time_high) * 100, 2) as pct_from_ath,
    ema_20,
    (close >= prev_ath AND close > ema_20) as is_breakout,
    (pct_from_ath >= -3.0 AND pct_from_ath < 0) as is_near_ath
FROM latest_day
WHERE rn = 1;
```

---

## 4. Supabase Database Schema (PostgreSQL DDL)

Supabase serves purely as a real-time caching layer for the UI. Execute this DDL inside your Supabase SQL Editor:

```sql
-- 1. Master Stock Table
CREATE TABLE public.stock_master (
    symbol VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    exchange VARCHAR(20) DEFAULT 'NSE',
    sector VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Daily Analytics Pulse Cache (< 100 MB footprint)
CREATE TABLE public.stock_analytics_pulse (
    symbol VARCHAR(50) PRIMARY KEY REFERENCES public.stock_master(symbol) ON DELETE CASCADE,
    last_price NUMERIC(12, 2) NOT NULL,
    all_time_high NUMERIC(12, 2) NOT NULL,
    ath_date DATE,
    pct_from_ath NUMERIC(6, 2) NOT NULL,      -- e.g., -1.45 (%)
    ema_20 NUMERIC(12, 2),
    volume_surge_ratio NUMERIC(6, 2),          -- e.g., 2.5x average
    is_breakout_candidate BOOLEAN DEFAULT FALSE,
    is_near_ath BOOLEAN DEFAULT FALSE,
    trend_status VARCHAR(50),                  -- 'BULLISH', 'CONSOLIDATING', 'BEARISH'
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes optimized for fast UI filtering & screeners
CREATE INDEX idx_pulse_near_ath ON public.stock_analytics_pulse(pct_from_ath DESC);
CREATE INDEX idx_pulse_breakout ON public.stock_analytics_pulse(is_breakout_candidate) WHERE is_breakout_candidate = TRUE;
CREATE INDEX idx_pulse_sector ON public.stock_analytics_pulse(symbol);

-- Enable Row Level Security (RLS)
ALTER TABLE public.stock_analytics_pulse ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow Public Read Access" ON public.stock_analytics_pulse FOR SELECT USING (true);
```

---

## 5. Implementation Prompt Instructions for Claude

Pass the following instructions directly to Claude when prompting it to write the code:

> **System Implementation Request for Claude:**
> 
> "Please implement the updated cost-optimized backend and analytics pipeline for the **Pulse Swing Trading Terminal** following the provided design specification.
> 
> **Specific Tasks to Implement:**
> 1. **Cloudflare R2 Parquet Handler:** Create a TypeScript/Python utility function using `@aws-sdk/client-s3` (or `pyarrow`) to read/write compressed Parquet daily candles to R2 bucket `pulse-market-data`.
> 2. **DuckDB Analytics Worker:** Write a Python/Node script that uses DuckDB to:
>    - Load daily candles directly from R2.
>    - Compute metrics (`all_time_high`, `pct_from_ath`, `ema_20`, `is_breakout_candidate`).
>    - UPSERT the aggregated result array into Supabase table `stock_analytics_pulse`.
> 3. **Supabase Data Fetcher API:** Create a Next.js App Router API Route (`app/api/pulse/route.ts`) that queries `stock_analytics_pulse` with filtering params (`near_ath=true`, `breakout=true`, `sector=...`).
> 4. **UI Updates:** Connect the 'Current Pulse' Dashboard page to fetch from `stock_analytics_pulse` using `@supabase/supabase-js` with server-side caching (`revalidate: 300`)."

---

## 6. Execution Roadmap & Milestones

1. **Phase 1 (Data Layer):** Upload your 20-year raw historical CSV/JSON dataset converted to Parquet into Cloudflare R2 bucket (`/daily/symbol=.../`).
2. **Phase 2 (Database Setup):** Run the Supabase DDL above to create lightweight table schemas and indexes.
3. **Phase 3 (Compute Automation):** Deploy the DuckDB script into a scheduled GitHub Action or Cloudflare Worker running post-market hours (4:30 PM IST daily).
4. **Phase 4 (UI Integration):** Bind Next.js Server Components to fetch screener results from Supabase.
