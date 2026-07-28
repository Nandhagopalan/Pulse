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
  try {
    const quotes = await getQuotes(token, Object.values(INDEX_QUOTE_KEYS));
    const at = new Date().toISOString();
    for (const [name, key] of Object.entries(INDEX_QUOTE_KEYS)) {
      const q = quotes[key];
      if (q?.last_price) cache.set(name, { last: q.last_price, prevClose: q.ohlc?.close ?? 0, at });
    }
  } catch (err) {
    console.warn('[live] index quote poll failed:', (err as Error).message);
  }
}
