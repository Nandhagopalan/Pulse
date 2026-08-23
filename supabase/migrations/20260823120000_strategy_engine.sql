-- Strategy engine: the rules-based swing book.
--
-- Design: docs/strategy-engine.md. Apply with `supabase db push`.
--
-- Four tables, written only by pipeline/compute/strategy and read only by
-- server/src/api.ts. Nothing here is in the request path's hot loop; the whole
-- book is a few thousand rows.
--
-- Type conventions follow the baseline rather than native Postgres types, and
-- deliberately:
--   * dates are TEXT ('YYYY-MM-DD'). A DATE column comes back through node-pg
--     as a JS Date and JSON-serialises to '2026-08-21T00:00:00.000Z', which the
--     UI would have to re-trim. Every other table in this schema already stores
--     ISO strings, so the API stays uniform.
--   * money is DOUBLE PRECISION, not NUMERIC and not REAL. NUMERIC comes back
--     from node-pg as a *string* to preserve precision, and the UI does
--     arithmetic on these. REAL is the trap: float4 carries ~7 significant
--     digits, and a compounding book at rupee scale needs 9 — equity of
--     5,169,360.42 does not survive the round trip, and the error compounds
--     into position sizing. Prices elsewhere in this schema are REAL because
--     they are quotes, not a ledger.
--   * the config blob is TEXT holding JSON, matching breadth_daily.data et al.

-- ── One row per book: a config plus the positions that belong to it ─────────
-- The full config is stored with the book so a parameter change is visible in
-- the data, and past results stay attributable to the parameters that made
-- them. Running a second configuration in parallel is a row here, not a code
-- change.
CREATE TABLE IF NOT EXISTS strategy_books (
  id         TEXT PRIMARY KEY,              -- 'balanced'
  enabled    BOOLEAN NOT NULL DEFAULT TRUE,
  fill_mode  TEXT    NOT NULL DEFAULT 'auto',   -- auto | manual
  config     TEXT    NOT NULL,              -- JSON: the current StrategyConfig
  config_version INTEGER NOT NULL DEFAULT 1, -- bumped on every config change
  capital    DOUBLE PRECISION    NOT NULL,              -- opening capital; later changes are
                                            -- cash flows, not returns (see below)
  started_on TEXT,                          -- first session this book traded
  created_at TEXT    NOT NULL,
  updated_at TEXT    NOT NULL
);

-- ── Daily book state; this is also the paper equity curve ──────────────────
CREATE TABLE IF NOT EXISTS strategy_state (
  book_id    TEXT NOT NULL,
  date       TEXT NOT NULL,
  regime_on  BOOLEAN NOT NULL,
  ew_index   DOUBLE PRECISION,                          -- the regime index and its MA, so
  ew_ma      DOUBLE PRECISION,                          -- the banner can show the margin
  universe_n INTEGER,
  equity     DOUBLE PRECISION NOT NULL,
  cash       DOUBLE PRECISION NOT NULL,
  deployed   DOUBLE PRECISION NOT NULL,                 -- fraction of equity in positions
  n_open     INTEGER NOT NULL DEFAULT 0,
  net_flow   DOUBLE PRECISION NOT NULL DEFAULT 0,        -- deposits/withdrawals settled today
  twr_factor DOUBLE PRECISION,                           -- daily return excluding that flow
  PRIMARY KEY (book_id, date)
);

-- ── Tomorrow morning's candidates, already sized ───────────────────────────
-- stop_pct and risk_amount are stored rather than recomputed: without them you
-- cannot tell from the UI why one position is Rs 1.9L and another Rs 3.8L, and
-- the sizing stops being auditable.
CREATE TABLE IF NOT EXISTS strategy_signals (
  book_id        TEXT NOT NULL,
  date           TEXT NOT NULL,             -- session the signal was computed on
  symbol         TEXT NOT NULL,
  rank           INTEGER NOT NULL,          -- 1 = strongest; fills in this order
  ref_close      DOUBLE PRECISION NOT NULL,             -- the close that triggered it
  stop           DOUBLE PRECISION NOT NULL,
  stop_pct       DOUBLE PRECISION NOT NULL,             -- (ref_close - stop) / ref_close
  atr            DOUBLE PRECISION,
  rs_pct         DOUBLE PRECISION,                      -- cross-sectional momentum rank 0..1
  sector         TEXT,
  turnover_20d   DOUBLE PRECISION,
  qty            INTEGER NOT NULL,
  position_value DOUBLE PRECISION NOT NULL,
  risk_amount    DOUBLE PRECISION NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending | filled | skipped
  skip_reason    TEXT,                      -- slots | sector_cap | adv | cash
  PRIMARY KEY (book_id, date, symbol)
);

-- ── The paper book ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_positions (
  id          BIGSERIAL PRIMARY KEY,
  book_id     TEXT NOT NULL,
  config_version INTEGER NOT NULL DEFAULT 1, -- which rules produced this trade
  origin      TEXT NOT NULL DEFAULT 'auto',  -- auto | manual
  symbol      TEXT NOT NULL,
  sector      TEXT,
  entry_date  TEXT NOT NULL,
  entry_px    DOUBLE PRECISION NOT NULL,                -- fill price, slippage included
  qty         INTEGER NOT NULL,
  init_stop   DOUBLE PRECISION NOT NULL,                -- kept so R multiples stay comparable
  stop        DOUBLE PRECISION NOT NULL,                -- current stop (ratchets only)
  r_per_share DOUBLE PRECISION NOT NULL,
  last_px     DOUBLE PRECISION,                         -- last traded close, for stale exits
  bars        INTEGER NOT NULL DEFAULT 0,   -- sessions held; drives the time stop
  stale       INTEGER NOT NULL DEFAULT 0,   -- consecutive sessions with no bar
  status      TEXT NOT NULL DEFAULT 'open', -- open | closed
  -- An exit decided on tonight's close is executed at tomorrow's open, so the
  -- decision has to survive the gap between two nightly runs.
  pending_exit TEXT,
  exit_date   TEXT,
  exit_px     DOUBLE PRECISION,
  exit_reason TEXT,                         -- stop | time | regime | stale
  pnl         DOUBLE PRECISION,
  r_multiple  DOUBLE PRECISION
);

-- Open positions are read every night and on every page view; the signal and
-- state tables are read newest-first.
CREATE INDEX IF NOT EXISTS idx_strategy_positions_open
  ON strategy_positions(book_id, status);
CREATE INDEX IF NOT EXISTS idx_strategy_positions_symbol
  ON strategy_positions(book_id, symbol, status);
CREATE INDEX IF NOT EXISTS idx_strategy_signals_date
  ON strategy_signals(book_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_state_date
  ON strategy_state(book_id, date DESC);


-- ── Config history ─────────────────────────────────────────────────────────
-- Tweaking a live book is expected — that is the point of the terminal. But a
-- book whose rules changed halfway has a performance record that belongs to
-- neither configuration, so every change is versioned and every position is
-- stamped with the version that produced it. Results can then be sliced by
-- version, and "what did the 0.6% risk setting actually do" stays answerable.
CREATE TABLE IF NOT EXISTS strategy_config_log (
  book_id    TEXT NOT NULL,
  version    INTEGER NOT NULL,
  config     TEXT NOT NULL,                 -- full StrategyConfig at this version
  changed_at TEXT NOT NULL,
  note       TEXT,                          -- why, in the operator's words
  PRIMARY KEY (book_id, version)
);

-- ── Cash flows ─────────────────────────────────────────────────────────────
-- Adding or withdrawing capital moves equity without being performance. A CAGR
-- read straight off the equity curve would count a deposit as a gain, so flows
-- are recorded separately and returns are chain-linked across them
-- (time-weighted), which is the only way the number stays comparable to the
-- backtest.
CREATE TABLE IF NOT EXISTS strategy_cashflows (
  id      BIGSERIAL PRIMARY KEY,
  book_id TEXT NOT NULL,
  date    TEXT NOT NULL,
  amount  DOUBLE PRECISION NOT NULL,                    -- positive deposit, negative withdrawal
  note    TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategy_cashflows_book
  ON strategy_cashflows(book_id, date);
