import type { IndexSeries, MarketData, Stock } from './data';

/**
 * Client-side symbol search over the published session.
 *
 * `/api/market/summary` already ships the whole active universe — every symbol
 * with a close on the last session, plus the index strip — so finding a stock
 * needs no round trip and no search endpoint. Filtering a few thousand rows per
 * keystroke is cheaper than the network call would be.
 *
 * There is no company name to match on: the pipeline reads one from the NSE
 * constituent lists but drops it before Postgres, so the terminal only ever
 * knows a ticker and its sector. Searching is therefore ticker-first, with
 * sector as a fallback so "banking" still returns the banks.
 */

export type SearchHit =
  | { kind: 'stock'; key: string; sub: string; stock: Stock }
  | { kind: 'index'; key: string; sub: string; index: IndexSeries };

/** Enough rows to scroll through without turning the palette into a table. */
export const MAX_HITS = 40;

/** Index names carry spaces ("NIFTY 50"); nobody types them. */
const norm = (s: string) => s.toUpperCase().replace(/\s+/g, '');

/**
 * Match quality, best first; -1 is no match.
 *
 * Ticker beats sector, so typing BANK lands on BANKBARODA before it lists
 * everything filed under Banking.
 */
function tier(key: string, sub: string, q: string): number {
  const k = norm(key);
  if (k === q) return 0;
  if (k.startsWith(q)) return 1;
  if (k.includes(q)) return 2;
  if (norm(sub).includes(q)) return 3;
  return -1;
}

/** The searchable pool for a session. Stable per payload, so memoise on `D`. */
export function candidates(D: MarketData): SearchHit[] {
  return [
    ...D.indices.map((index): SearchHit => ({ kind: 'index', key: index.name, sub: 'Index', index })),
    ...D.stocks.map((stock): SearchHit => ({ kind: 'stock', key: stock.sym, sub: stock.sector, stock })),
  ];
}

export function searchSymbols(pool: SearchHit[], query: string, watch: Record<string, true>): SearchHit[] {
  const q = norm(query);

  // Nothing typed yet: offer the places you are most likely headed rather than
  // an arbitrary alphabetical slice of 2,500 tickers.
  if (!q) {
    return [
      ...pool.filter(h => h.kind === 'index'),
      ...pool.filter(h => h.kind === 'stock' && watch[h.key]),
    ].slice(0, MAX_HITS);
  }

  const scored: { hit: SearchHit; t: number }[] = [];
  for (const hit of pool) {
    const t = tier(hit.key, hit.sub, q);
    if (t >= 0) scored.push({ hit, t });
  }

  // Within a tier: what you already track, then the shortest ticker (a prefix
  // search for TATA should reach TATAPOWER before TATACONSUM), then A–Z.
  scored.sort((a, b) =>
    a.t - b.t
    || (watch[b.hit.key] ? 1 : 0) - (watch[a.hit.key] ? 1 : 0)
    || a.hit.key.length - b.hit.key.length
    || a.hit.key.localeCompare(b.hit.key));

  return scored.slice(0, MAX_HITS).map(s => s.hit);
}

/**
 * Narrow an already-scoped table to what matches `query`.
 *
 * Shares `tier()` with the palette so a ticker means the same thing whether you
 * are jumping to it or filtering for it. Order is left alone: a table has its
 * own sort, and re-ranking rows under the user's chosen column would fight it.
 */
export function filterStocks(stocks: Stock[], query: string): Stock[] {
  const q = norm(query);
  if (!q) return stocks;
  return stocks.filter(s => tier(s.sym, s.sector, q) >= 0);
}
