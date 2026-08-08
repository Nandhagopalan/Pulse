import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, '..', '..'); // repo root
export const SERVER_DIR = resolve(HERE, '..');

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

/** Comma/whitespace-separated email list → lowercased set. */
function parseEmailList(raw: string): Set<string> {
  return new Set(raw.split(/[,\s]+/).map(s => s.trim().toLowerCase()).filter(Boolean));
}

const appUrl = env('APP_URL', 'http://localhost:5173');

export const config = {
  port: Number(env('PORT', '8000')),

  /** Google OAuth client (console.cloud.google.com → Credentials → OAuth client ID). */
  googleClientId: env('GOOGLE_CLIENT_ID'),
  googleClientSecret: env('GOOGLE_CLIENT_SECRET'),
  /** Must exactly match an Authorized redirect URI on the OAuth client. */
  googleRedirectUrl: env('GOOGLE_REDIRECT_URL', 'http://localhost:5173/auth/google/callback'),
  /**
   * Access control. Default is closed: only listed emails may sign in, so a
   * deployed instance is never accidentally open to every Google account.
   * Set ALLOW_ALL_SIGNUPS=1 to open it up once that decision is made.
   */
  allowedEmails: parseEmailList(env('ALLOWED_EMAILS')),
  allowAllSignups: env('ALLOW_ALL_SIGNUPS', '') === '1',

  /** Where to send the browser after a successful login. */
  appUrl,
  /**
   * Mark session cookies `Secure` when the app is served over HTTPS. Derived
   * from APP_URL rather than a NODE_ENV guess: the deployment that needs the
   * flag is exactly the one whose app URL is https, and localhost — where a
   * Secure cookie would simply never be stored — stays working untouched.
   */
  secureCookies: appUrl.startsWith('https://'),
  /**
   * Required: postgres://... — there is no local-file fallback. SUPABASE_DB_URL
   * is the same thing under the name Supabase uses. For local development,
   * `supabase start` runs the stack in Docker and prints a URI to use here.
   */
  databaseUrl: env('DATABASE_URL') || env('SUPABASE_DB_URL'),
  /** Connections per instance. Small on purpose — see the comment in db.ts. */
  dbPoolMax: Number(env('DB_POOL_MAX', '3')),
  /** Dev only: when '1', GET /auth/dev-login creates a session without Google. */
  devLogin: env('DEV_LOGIN', '') === '1',
  /** Marketaux news API token (marketaux.com). News features disabled if unset.
   *  Accepts MARKETAUX_API_KEY or the shorter MARKET_AUX_KEY. */
  marketauxApiKey: env('MARKETAUX_API_KEY') || env('MARKET_AUX_KEY'),
  /** Minutes between watchlist news refreshes (free tier = 100 req/day; keep ≥30). */
  newsRefreshMin: Number(env('NEWS_REFRESH_MIN', '30')),
  /** Suffix Marketaux uses for NSE symbols (RELIANCE → RELIANCE.NS). */
  newsSymbolSuffix: env('NEWS_SYMBOL_SUFFIX', '.NS'),
  /**
   * Symbols batched per Marketaux request (comma-separated); free tier caps
   * articles. This doubles as the per-view refresh ceiling: a page view spends
   * exactly one request, so the shared 100/day budget degrades to slower
   * rotation across a large watchlist rather than sudden exhaustion.
   */
  newsSymbolsPerReq: Number(env('NEWS_SYMBOLS_PER_REQ', '5')),
};

if (!config.googleClientId || !config.googleClientSecret) {
  console.warn('[config] GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing — Google sign-in will not work.');
} else if (!config.allowAllSignups && config.allowedEmails.size === 0) {
  console.warn(
    '[config] No ALLOWED_EMAILS set and ALLOW_ALL_SIGNUPS is off — every Google sign-in will be '
    + 'rejected. List the emails that may sign in, or set ALLOW_ALL_SIGNUPS=1 to open it up.',
  );
}
if (!config.marketauxApiKey) {
  console.warn('[config] MARKETAUX_API_KEY missing — watchlist news will be unavailable.');
}
if (!config.databaseUrl) {
  console.warn(
    '[config] No SUPABASE_DB_URL / DATABASE_URL — the server will refuse to start. '
    + 'Run `supabase start` for a local stack and use the URI it prints.',
  );
}
