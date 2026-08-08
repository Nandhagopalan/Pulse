/**
 * Storage adapter. Defaults to embedded SQLite (node:sqlite) for a zero-setup
 * dev experience; set DATABASE_URL=postgres://... to run on PostgreSQL/TimescaleDB.
 * SQL is written in the shared subset (ON CONFLICT upserts, TEXT dates, ? params —
 * translated to $n for Postgres).
 */
import { resolve } from 'node:path';
import { mkdirSync } from 'node:fs';
import { config, DATA_DIR } from './config.ts';

export interface Db {
  all<T = Record<string, unknown>>(sql: string, params?: unknown[]): Promise<T[]>;
  run(sql: string, params?: unknown[]): Promise<void>;
  exec(sql: string): Promise<void>;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS instruments (
  symbol TEXT PRIMARY KEY,
  name TEXT, isin TEXT, series TEXT,
  industry TEXT,            -- fine-grained industry (from index constituent files)
  sector TEXT,              -- coarse sector used for aggregation
  kite_token INTEGER,
  active INTEGER DEFAULT 1,
  listing_date TEXT
);
CREATE TABLE IF NOT EXISTS index_membership (
  index_name TEXT, symbol TEXT,
  PRIMARY KEY (index_name, symbol)
);
CREATE TABLE IF NOT EXISTS daily_bars (
  symbol TEXT, date TEXT,
  open REAL, high REAL, low REAL, close REAL, prev_close REAL,
  volume REAL, traded_value REAL, trades INTEGER, delivery_pct REAL,
  PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(date);
CREATE TABLE IF NOT EXISTS index_bars (
  index_name TEXT, date TEXT,
  open REAL, high REAL, low REAL, close REAL,
  PRIMARY KEY (index_name, date)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
  symbol TEXT, ex_date TEXT, kind TEXT,
  factor REAL,              -- k: adjusted = raw / k for bars before ex_date
  detail TEXT,
  PRIMARY KEY (symbol, ex_date, kind)
);
CREATE TABLE IF NOT EXISTS fii_dii (
  date TEXT, category TEXT,  -- 'FII' | 'DII'
  buy REAL, sell REAL, net REAL,
  PRIMARY KEY (date, category)
);
CREATE TABLE IF NOT EXISTS breadth_daily (
  date TEXT PRIMARY KEY,
  data TEXT                 -- JSON: aggregates for the session
);
CREATE TABLE IF NOT EXISTS sector_scores (
  date TEXT, sector TEXT, data TEXT,
  PRIMARY KEY (date, sector)
);
CREATE TABLE IF NOT EXISTS stock_metrics (
  date TEXT, symbol TEXT, data TEXT,
  PRIMARY KEY (date, symbol)
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
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ingest_log (
  ts TEXT, job TEXT, date TEXT, status TEXT, detail TEXT
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
CREATE INDEX IF NOT EXISTS idx_news_symbol_date ON news_articles(symbol, published_at DESC);
CREATE TABLE IF NOT EXISTS news_interest (
  user_id TEXT, symbol TEXT,
  last_seen TEXT,            -- ISO — last time this user asked for the symbol's news
  PRIMARY KEY (user_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_news_interest_seen ON news_interest(last_seen);
CREATE TABLE IF NOT EXISTS user_watchlist (
  user_id TEXT, symbol TEXT,
  added_at TEXT,
  PRIMARY KEY (user_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_user_watchlist_user ON user_watchlist(user_id);
CREATE TABLE IF NOT EXISTS user_prefs (
  user_id TEXT PRIMARY KEY,
  data TEXT,                 -- JSON: capital, riskPct, maxPos
  updated_at TEXT
);
`;

class SqliteDb implements Db {
  private db: import('node:sqlite').DatabaseSync;
  constructor(path: string) {
    // Dynamic require keeps the experimental-module warning localized.
    const { DatabaseSync } = require('node:sqlite') as typeof import('node:sqlite');
    this.db = new DatabaseSync(path);
    this.db.exec('PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;');
  }
  async all<T>(sql: string, params: unknown[] = []): Promise<T[]> {
    return this.db.prepare(sql).all(...(params as never[])) as T[];
  }
  async run(sql: string, params: unknown[] = []): Promise<void> {
    this.db.prepare(sql).run(...(params as never[]));
  }
  async exec(sql: string): Promise<void> {
    this.db.exec(sql);
  }
}

class PgDb implements Db {
  private pool: { query: (sql: string, params?: unknown[]) => Promise<{ rows: unknown[] }> };
  constructor(pool: PgDb['pool']) { this.pool = pool; }
  static async connect(url: string): Promise<PgDb> {
    const { default: pg } = await import('pg');
    return new PgDb(new pg.Pool({ connectionString: url }));
  }
  private translate(sql: string): string {
    let n = 0;
    return sql.replace(/\?/g, () => `$${++n}`);
  }
  async all<T>(sql: string, params: unknown[] = []): Promise<T[]> {
    return (await this.pool.query(this.translate(sql), params)).rows as T[];
  }
  async run(sql: string, params: unknown[] = []): Promise<void> {
    await this.pool.query(this.translate(sql), params);
  }
  async exec(sql: string): Promise<void> {
    await this.pool.query(sql);
  }
}

// node:sqlite is ESM-importable but we are in ESM; emulate require via createRequire.
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);

let dbPromise: Promise<Db> | null = null;

export function getDb(): Promise<Db> {
  if (!dbPromise) {
    dbPromise = (async () => {
      let db: Db;
      if (config.databaseUrl) {
        db = await PgDb.connect(config.databaseUrl);
        console.log('[db] connected to Postgres');
      } else {
        mkdirSync(DATA_DIR, { recursive: true });
        const path = resolve(DATA_DIR, 'pulse.db');
        db = new SqliteDb(path);
        console.log('[db] using SQLite at', path);
      }
      await db.exec(SCHEMA);
      return db;
    })();
  }
  return dbPromise;
}

export async function metaGet(key: string): Promise<string | null> {
  const db = await getDb();
  const rows = await db.all<{ value: string }>('SELECT value FROM meta WHERE key = ?', [key]);
  return rows[0]?.value ?? null;
}

export async function metaSet(key: string, value: string): Promise<void> {
  const db = await getDb();
  await db.run(
    'INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
    [key, value],
  );
}

export async function logIngest(job: string, date: string, status: string, detail = ''): Promise<void> {
  const db = await getDb();
  await db.run('INSERT INTO ingest_log (ts, job, date, status, detail) VALUES (?, ?, ?, ?, ?)', [
    new Date().toISOString(), job, date, status, detail,
  ]);
}
