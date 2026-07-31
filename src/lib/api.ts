import type { MarketData } from './data';

export interface SessionUser {
  id: string;
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

export function fetchMe(): Promise<{ user: SessionUser; kiteConnected: boolean }> {
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

export function fetchNews(symbols: string[]): Promise<{ enabled: boolean; articles: NewsArticle[] }> {
  const qs = encodeURIComponent(symbols.join(','));
  return request(`/api/news?symbols=${qs}`);
}

export const kiteLoginUrl = '/auth/kite/login';
