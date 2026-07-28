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
  /** Optional: postgres://... — falls back to SQLite in server/data/pulse.db. */
  databaseUrl: env('DATABASE_URL'),
  /** Number of trading sessions of history to backfill on bootstrap. */
  backfillSessions: Number(env('BACKFILL_SESSIONS', '270')),
  /** Dev only: when '1', GET /auth/dev-login creates a session without Kite. */
  devLogin: env('DEV_LOGIN', '') === '1',
};

if (!config.kiteApiKey || !config.kiteApiSecret) {
  console.warn('[config] ZERODHA_API_KEY / ZERODHA_API_SECRET missing — Kite SSO will not work.');
}
