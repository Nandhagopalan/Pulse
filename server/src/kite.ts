/**
 * Zerodha Kite Connect REST client (no SDK dependency — the API is plain HTTPS).
 * Docs: https://kite.trade/docs/connect/v3/
 */
import { createHash } from 'node:crypto';
import { config } from './config.ts';

const BASE = 'https://api.kite.trade';

export interface KiteProfile {
  user_id: string;
  user_name: string;
  email: string;
  avatar_url: string | null;
}

export interface KiteSession extends KiteProfile {
  access_token: string;
}

export function loginUrl(): string {
  return `https://kite.zerodha.com/connect/login?v=3&api_key=${encodeURIComponent(config.kiteApiKey)}`;
}

async function kiteFetch<T>(path: string, opts: {
  method?: string;
  accessToken?: string;
  form?: Record<string, string>;
} = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Kite-Version': '3' };
  if (opts.accessToken) headers['Authorization'] = `token ${config.kiteApiKey}:${opts.accessToken}`;
  let body: string | undefined;
  if (opts.form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    body = new URLSearchParams(opts.form).toString();
  }
  const res = await fetch(BASE + path, { method: opts.method ?? 'GET', headers, body, signal: AbortSignal.timeout(30000) });
  const text = await res.text();
  if (!res.ok) {
    let message = text;
    try { message = (JSON.parse(text) as { message?: string }).message ?? text; } catch { /* raw text */ }
    throw new KiteError(res.status, message);
  }
  // /instruments returns CSV, everything else JSON envelopes {status, data}.
  if (res.headers.get('content-type')?.includes('csv')) return text as unknown as T;
  return (JSON.parse(text) as { data: T }).data;
}

export class KiteError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Exchange the request_token from the login redirect for an access token + profile. */
export async function exchangeToken(requestToken: string): Promise<KiteSession> {
  const checksum = createHash('sha256')
    .update(config.kiteApiKey + requestToken + config.kiteApiSecret)
    .digest('hex');
  return kiteFetch<KiteSession>('/session/token', {
    method: 'POST',
    form: { api_key: config.kiteApiKey, request_token: requestToken, checksum },
  });
}

export async function getProfile(accessToken: string): Promise<KiteProfile> {
  return kiteFetch<KiteProfile>('/user/profile', { accessToken });
}

/** Full NSE instrument dump (CSV text): tradingsymbol → instrument_token, name, etc. */
export async function getInstrumentsCsv(accessToken: string, exchange = 'NSE'): Promise<string> {
  return kiteFetch<string>(`/instruments/${exchange}`, { accessToken });
}

/** Batched quotes; keys like "NSE:INFY" or "NSE:NIFTY 50". Max ~500 per call. */
export async function getQuotes(accessToken: string, keys: string[]): Promise<Record<string, {
  last_price: number;
  ohlc?: { open: number; high: number; low: number; close: number };
  volume?: number;
}>> {
  const qs = keys.map(k => 'i=' + encodeURIComponent(k)).join('&');
  return kiteFetch(`/quote?${qs}`, { accessToken });
}

/** Daily historical candles (requires the historical data add-on on the Kite app). */
export async function getDailyCandles(accessToken: string, instrumentToken: number, from: string, to: string): Promise<
  [string, number, number, number, number, number][]
> {
  const data = await kiteFetch<{ candles: [string, number, number, number, number, number][] }>(
    `/instruments/historical/${instrumentToken}/day?from=${from}&to=${to}`,
    { accessToken },
  );
  return data.candles;
}
