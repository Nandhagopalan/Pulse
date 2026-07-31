/**
 * Watchlist news via the Marketaux API (marketaux.com).
 *
 * Free tier is 100 requests/day, so we batch several symbols per request and
 * cache everything in `news_articles`. A background job (scheduler) refreshes
 * the symbols any client has recently asked for; the API serves straight from
 * the cache so the UI never waits on Marketaux.
 *
 * NSE symbols are suffixed for Marketaux (RELIANCE → RELIANCE.NS by default).
 */
import { getDb, logIngest } from './db.ts';
import { config } from './config.ts';

const BASE = 'https://api.marketaux.com/v1/news/all';

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

interface MxEntity { symbol?: string; type?: string; sentiment_score?: number }
interface MxArticle {
  uuid: string;
  title: string;
  description: string | null;
  url: string;
  source: string;
  image_url: string | null;
  published_at: string;
  entities?: MxEntity[];
}

/** RELIANCE → RELIANCE.NS ; strip the suffix on the way back. */
const toMxSymbol = (sym: string) => sym.includes('.') ? sym : sym + config.newsSymbolSuffix;
const fromMxSymbol = (mx: string) => mx.replace(new RegExp(escapeRe(config.newsSymbolSuffix) + '$'), '');
function escapeRe(s: string) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

/** True when news is configured and usable. */
export const newsEnabled = () => !!config.marketauxApiKey;

/**
 * Fetch news for a batch of symbols from Marketaux and upsert into the cache.
 * Returns the number of (article, symbol) rows written. One request per call.
 */
export async function fetchNewsBatch(symbols: string[]): Promise<number> {
  if (!config.marketauxApiKey || symbols.length === 0) return 0;
  const mxSymbols = symbols.map(toMxSymbol).join(',');
  const params = new URLSearchParams({
    api_token: config.marketauxApiKey,
    symbols: mxSymbols,
    filter_entities: 'true',   // only articles actually tagged to these entities
    language: 'en',
    limit: '3',                // free tier caps articles/request; keep small, batch symbols
  });

  let articles: MxArticle[];
  try {
    const res = await fetch(`${BASE}?${params}`, { signal: AbortSignal.timeout(20000) });
    if (!res.ok) {
      await logIngest('news', symbols.join(','), 'failed', `status ${res.status}`);
      return 0;
    }
    const body = await res.json() as { data?: MxArticle[]; error?: { message?: string } };
    if (body.error) { await logIngest('news', symbols.join(','), 'failed', body.error.message ?? 'api error'); return 0; }
    articles = body.data ?? [];
  } catch (err) {
    await logIngest('news', symbols.join(','), 'failed', String(err));
    return 0;
  }

  const wanted = new Set(symbols);
  const now = new Date().toISOString();
  const db = await getDb();
  let written = 0;

  for (const a of articles) {
    // Fan the article out to each of our watchlist symbols it is tagged to.
    const tagged = new Map<string, number | null>();
    for (const e of a.entities ?? []) {
      if (!e.symbol) continue;
      const sym = fromMxSymbol(e.symbol);
      if (wanted.has(sym)) tagged.set(sym, typeof e.sentiment_score === 'number' ? e.sentiment_score : null);
    }
    for (const [sym, sentiment] of tagged) {
      await db.run(
        `INSERT INTO news_articles (id, symbol, title, description, url, source, image_url, sentiment, published_at, fetched_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id, symbol) DO UPDATE SET
           title = excluded.title, description = excluded.description, sentiment = excluded.sentiment`,
        [a.uuid, sym, a.title, a.description ?? '', a.url, a.source, a.image_url, sentiment, a.published_at, now],
      );
      written++;
    }
  }
  await logIngest('news', symbols.join(','), 'ok', `${articles.length} articles → ${written} rows`);
  return written;
}

/** Record that a client is interested in these symbols (drives the refresh job). */
export async function registerWatched(symbols: string[]): Promise<void> {
  if (symbols.length === 0) return;
  const db = await getDb();
  const now = new Date().toISOString();
  for (const sym of symbols) {
    await db.run(
      `INSERT INTO watched_symbols (symbol, last_seen) VALUES (?, ?)
       ON CONFLICT(symbol) DO UPDATE SET last_seen = excluded.last_seen`,
      [sym, now],
    );
  }
}

/** Read cached news for the given symbols, newest first. */
export async function getCachedNews(symbols: string[], limit = 60): Promise<NewsArticle[]> {
  if (symbols.length === 0) return [];
  const db = await getDb();
  const placeholders = symbols.map(() => '?').join(',');
  const rows = await db.all<{
    id: string; symbol: string; title: string; description: string; url: string;
    source: string; image_url: string | null; sentiment: number | null; published_at: string;
  }>(
    `SELECT id, symbol, title, description, url, source, image_url, sentiment, published_at
     FROM news_articles WHERE symbol IN (${placeholders})
     ORDER BY published_at DESC LIMIT ?`,
    [...symbols, limit],
  );
  return rows.map(r => ({
    id: r.id, symbol: r.symbol, title: r.title, description: r.description, url: r.url,
    source: r.source, imageUrl: r.image_url, sentiment: r.sentiment, publishedAt: r.published_at,
  }));
}

/**
 * Refresh news for every symbol seen by a client in the last 24h.
 * Batched to respect the free-tier request budget. Called by the scheduler.
 */
export async function refreshWatchlistNews(): Promise<void> {
  if (!config.marketauxApiKey) return;
  const db = await getDb();
  const cutoff = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const rows = await db.all<{ symbol: string }>(
    'SELECT symbol FROM watched_symbols WHERE last_seen >= ? ORDER BY symbol', [cutoff],
  );
  const symbols = rows.map(r => r.symbol);
  if (symbols.length === 0) return;

  const per = Math.max(1, config.newsSymbolsPerReq);
  for (let i = 0; i < symbols.length; i += per) {
    await fetchNewsBatch(symbols.slice(i, i + per));
  }
  console.log(`[news] refreshed ${symbols.length} watched symbols`);
}
