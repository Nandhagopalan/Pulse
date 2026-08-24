import type { MarketData } from './data';

export interface SessionUser {
  id: string;
  provider: string;
  name: string;
  email: string;
  avatar: string | null;
}

export interface BackfillStatus {
  running: boolean;
  done: number;
  target: number;
  currentDate: string | null;
  error: string | null;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super('api_error_' + status);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export function fetchMe(): Promise<{ user: SessionUser }> {
  return request('/auth/me');
}

export function logout(): Promise<void> {
  return request('/auth/logout', { method: 'POST' });
}

export function fetchSummary(): Promise<MarketData & { date: string }> {
  return request('/api/market/summary');
}

export interface ApiCandle { d: string; o: number; h: number; l: number; c: number; v: number }

export function fetchCandles(sym: string, n = 180): Promise<{ sym: string; candles: ApiCandle[] }> {
  return request(`/api/stocks/${encodeURIComponent(sym)}/candles?n=${n}`);
}

export interface NewsArticle {
  id: string;
  symbol: string;
  title: string;
  description: string;
  url: string;
  source: string;
  imageUrl: string | null;
  sentiment: number | null;
  publishedAt: string;
}

/** Symbols come from the signed-in account's watchlist, server-side. */
export function fetchNews(): Promise<{ enabled: boolean; articles: NewsArticle[] }> {
  return request('/api/news');
}

// ---- Per-user state ----

export function fetchWatchlist(): Promise<{ symbols: string[] }> {
  return request('/api/watchlist');
}

export function addToWatchlist(sym: string): Promise<{ symbols: string[] }> {
  return request(`/api/watchlist/${encodeURIComponent(sym)}`, { method: 'POST' });
}

export function removeFromWatchlist(sym: string): Promise<{ symbols: string[] }> {
  return request(`/api/watchlist/${encodeURIComponent(sym)}`, { method: 'DELETE' });
}

export function importWatchlist(symbols: string[]): Promise<{ symbols: string[] }> {
  return request('/api/watchlist/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols }),
  });
}

export const googleLoginUrl = '/auth/google/login';

/** Human-readable reason for a bounced sign-in, from the `?login=` query param. */
export function loginErrorMessage(search: string): string | null {
  const params = new URLSearchParams(search);
  const status = params.get('login');
  if (!status) return null;
  if (status === 'denied') return 'That account is not on the access list for this instance.';
  if (status === 'cancelled') return 'Sign-in was cancelled.';
  if (status !== 'error') return null;
  const reason = params.get('reason');
  if (reason === 'email_unverified') return 'Your Google account has no verified email address.';
  if (reason === 'bad_state') return 'Sign-in expired or was tampered with. Please try again.';
  return 'Sign-in failed. Please try again.';
}

// ── Strategy engine ─────────────────────────────────────────────────────────

export interface StrategySignal {
  symbol: string; rank: number; ref_close: number; stop: number; stop_pct: number;
  atr: number | null; rs_pct: number | null; sector: string | null;
  turnover_20d: number | null; qty: number; position_value: number; risk_amount: number;
  status: string; skip_reason: string | null;
}

export interface StrategyPosition {
  id: number; symbol: string; sector: string | null; entry_date: string; entry_px: number;
  qty: number; init_stop: number; stop: number; r_per_share: number; last_px: number | null;
  bars: number; pending_exit: string | null; config_version: number; origin: string;
}

export interface StrategyClosed {
  symbol: string; sector: string | null; entry_date: string; entry_px: number;
  exit_date: string; exit_px: number; qty: number; bars: number;
  exit_reason: string; pnl: number; r_multiple: number; origin: string;
}

export interface StrategyState {
  date: string; regime_on: boolean; ew_index: number | null; ew_ma: number | null;
  universe_n: number | null; equity: number; cash: number; deployed: number; n_open: number;
}

export interface StrategySummary {
  book: {
    id: string; fillMode: string; configVersion: number; capital: number;
    startedOn: string | null; config: Record<string, unknown> | null;
  };
  books: string[];
  state: StrategyState | null;
  performance: {
    days: number; equity?: number; capital?: number;
    totalReturn: number | null; cagr: number | null; maxDrawdown: number | null;
  };
  equity: { date: string; equity: number; deployed: number; regime_on: boolean }[];
  positions: StrategyPosition[];
  signals: StrategySignal[];
  closed: StrategyClosed[];
}

export interface NewPosition {
  book: string; symbol: string; entry_date: string; entry_px: number;
  stop: number; qty: number; last_px?: number; sector?: string | null;
}

/** Fields of an open manual position that can be corrected. All optional. */
export interface PositionEdit {
  entry_date?: string; entry_px?: number; stop?: number; qty?: number; last_px?: number;
}

export function addStrategyPosition(p: NewPosition): Promise<{ ok: true }> {
  return request('/api/strategy/positions', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(p),
  });
}

export function editStrategyPosition(id: number, p: PositionEdit): Promise<{ ok: true }> {
  return request(`/api/strategy/positions/${id}/edit`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(p),
  });
}

export function closeStrategyPosition(
  id: number, exit_date: string, exit_px: number, reason?: string,
): Promise<{ ok: true; pnl: number }> {
  return request(`/api/strategy/positions/${id}/close`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ exit_date, exit_px, reason }),
  });
}

export function resetStrategy(capital: number): Promise<{ ok: true; capital: number }> {
  return request('/api/strategy/reset', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ capital, confirm: true }),
  });
}

export function fetchStrategy(book?: string): Promise<StrategySummary> {
  return request<StrategySummary>('/api/strategy/summary' + (book ? `?book=${encodeURIComponent(book)}` : ''));
}
