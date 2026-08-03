/**
 * Live quote cache. During market hours (09:15–15:30 IST) the scheduler polls
 * Kite REST quotes for the index strip using the freshest user access token.
 * (Full tick-level WebSocket streaming can slot in behind the same cache later.)
 */
import { getQuotes } from './kite.ts';
import { serviceAccessToken } from './auth.ts';
import { istMinutes, istNow } from './util.ts';

export const INDEX_QUOTE_KEYS: Record<string, string> = {
  'NIFTY 50': 'NSE:NIFTY 50',
  'NIFTY BANK': 'NSE:NIFTY BANK',
  'NIFTY MIDCAP 100': 'NSE:NIFTY MIDCAP 100',
  'NIFTY SMLCAP 100': 'NSE:NIFTY SMLCAP 100',
  'NIFTY IT': 'NSE:NIFTY IT',
  'INDIA VIX': 'NSE:INDIA VIX',
};

export interface LiveQuote { last: number; prevClose: number; at: string }

const cache = new Map<string, LiveQuote>();

/**
 * Kite access tokens expire daily (~06:00 IST). A long-running server crosses
 * that boundary with a token it cannot renew on its own — only a fresh Kite
 * login can. Retrying every tick then just spams the log and burns rate limit,
 * so once a token is rejected we park the poller against that exact token and
 * wake up only when a newer one is stored.
 */
let rejectedToken: string | null = null;

/** Kite signals a dead/incorrect token with 403 TokenException. */
function isAuthError(err: unknown): boolean {
  const status = (err as { status?: number }).status;
  const msg = (err as Error).message ?? '';
  return status === 403 || /api_key|access_token|token/i.test(msg);
}

/** Called after a successful Kite login so the poller resumes immediately. */
export function resetLiveAuthBlock(): void {
  rejectedToken = null;
}

/** True when the poller is parked waiting for a fresh login. */
export function liveAuthBlocked(): boolean {
  return rejectedToken !== null;
}

export function isMarketOpen(): boolean {
  const dow = istNow().getUTCDay();
  const mins = istMinutes();
  return dow >= 1 && dow <= 5 && mins >= 555 && mins <= 930;
}

export function liveQuote(name: string): LiveQuote | null {
  return cache.get(name) ?? null;
}

export async function pollIndexQuotes(): Promise<void> {
  if (!isMarketOpen()) return;
  const token = await serviceAccessToken();
  if (!token) return;
  // A newer token means someone logged in again — clear the block and retry.
  if (rejectedToken && token === rejectedToken) return;
  if (rejectedToken) rejectedToken = null;

  try {
    const quotes = await getQuotes(token, Object.values(INDEX_QUOTE_KEYS));
    const at = new Date().toISOString();
    for (const [name, key] of Object.entries(INDEX_QUOTE_KEYS)) {
      const q = quotes[key];
      if (q?.last_price) cache.set(name, { last: q.last_price, prevClose: q.ohlc?.close ?? 0, at });
    }
  } catch (err) {
    if (isAuthError(err)) {
      rejectedToken = token;
      console.warn(
        '[live] Kite access token rejected (expired — they last one trading day). '
        + 'Live index quotes are paused; sign in again at /auth/kite/login to resume. '
        + 'Stored EOD data is unaffected.',
      );
      return;
    }
    console.warn('[live] index quote poll failed:', (err as Error).message);
  }
}
