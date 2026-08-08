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

export interface StoredPrefs { capital: number; riskPct: number; maxPos: number }

export function fetchPrefs(): Promise<{ prefs: StoredPrefs | null }> {
  return request('/api/prefs');
}

export function savePrefs(prefs: StoredPrefs): Promise<{ prefs: StoredPrefs }> {
  return request('/api/prefs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(prefs),
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
