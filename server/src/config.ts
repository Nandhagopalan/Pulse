import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, '..', '..'); // repo root
export const SERVER_DIR = resolve(HERE, '..');
export const DATA_DIR = resolve(SERVER_DIR, 'data');

function parseEnvFile(path: string): Record<string, string> {
  try {
    const out: Record<string, string> = {};
    for (const raw of readFileSync(path, 'utf8').split('\n')) {
      const line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      const eq = line.indexOf('=');
      if (eq < 0) continue;
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      out[key] = val;
    }
    return out;
  } catch {
    return {};
  }
}

const fileEnv = { ...parseEnvFile(resolve(ROOT, '.env')), ...parseEnvFile(resolve(ROOT, '.env.local')) };
const env = (key: string, fallback = '') => process.env[key] ?? fileEnv[key] ?? fallback;

export const config = {
  port: Number(env('PORT', '8000')),
  kiteApiKey: env('ZERODHA_API_KEY'),
  kiteApiSecret: env('ZERODHA_API_SECRET'),
  /** Must match the Redirect URL registered on developers.kite.trade for this app. */
  kiteRedirectUrl: env('KITE_REDIRECT_URL', 'http://localhost:5173/auth/kite/callback'),
  /** Where to send the browser after a successful login. */
  appUrl: env('APP_URL', 'http://localhost:5173'),
  /** Optional: postgres://... — falls back to SQLite in server/data/pulse.db.
   *  SUPABASE_DB_URL is the same thing under the name Supabase uses. */
  databaseUrl: env('DATABASE_URL') || env('SUPABASE_DB_URL'),
  /**
   * When true, the Python pipeline (pipeline/) owns ingestion and analytics:
   * it writes the derived tables into Supabase and keeps the 20-year bar history
   * on R2. The Node server then only serves and does live quotes — running both
   * ingest paths against one database would have them overwrite each other.
   * Defaults on whenever a Supabase URL is configured; set PIPELINE_MODE=0 to
   * force the legacy self-ingesting behaviour.
   */
  pipelineMode: Boolean(env('SUPABASE_DB_URL')) && env('PIPELINE_MODE', '1') !== '0',
  /** Number of trading sessions of history to backfill on bootstrap.
   *  ~2500 ≈ 10 years, which is what true all-time-high detection needs. */
  backfillSessions: Number(env('BACKFILL_SESSIONS', '2500')),
  /** Dev only: when '1', GET /auth/dev-login creates a session without Kite. */
  devLogin: env('DEV_LOGIN', '') === '1',
  /** Marketaux news API token (marketaux.com). News features disabled if unset.
   *  Accepts MARKETAUX_API_KEY or the shorter MARKET_AUX_KEY. */
  marketauxApiKey: env('MARKETAUX_API_KEY') || env('MARKET_AUX_KEY'),
  /** Minutes between watchlist news refreshes (free tier = 100 req/day; keep ≥30). */
  newsRefreshMin: Number(env('NEWS_REFRESH_MIN', '30')),
  /** Suffix Marketaux uses for NSE symbols (RELIANCE → RELIANCE.NS). */
  newsSymbolSuffix: env('NEWS_SYMBOL_SUFFIX', '.NS'),
  /** Symbols batched per Marketaux request (comma-separated); free tier caps articles. */
  newsSymbolsPerReq: Number(env('NEWS_SYMBOLS_PER_REQ', '5')),
};

if (!config.kiteApiKey || !config.kiteApiSecret) {
  console.warn('[config] ZERODHA_API_KEY / ZERODHA_API_SECRET missing — Kite SSO will not work.');
}
if (!config.marketauxApiKey) {
  console.warn('[config] MARKETAUX_API_KEY missing — watchlist news will be unavailable.');
}
