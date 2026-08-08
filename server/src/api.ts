/** Terminal-facing API. All routes require an authenticated session. */
import { getDb, metaGet } from './db.ts';
import { requireAuth } from './auth.ts';
import { json, readJson, type Router } from './router.ts';
import { isMarketOpen } from './util.ts';
import { getCachedNews, refreshIfStale, newsEnabled } from './news.ts';

/** Display name → name used in the NSE all-indices close file (uppercased). */
const INDEX_STRIP: [string, string][] = [
  ['NIFTY 50', 'NIFTY 50'],
  ['NIFTY BANK', 'NIFTY BANK'],
  ['NIFTY MIDCAP 100', 'NIFTY MIDCAP 100'],
  ['NIFTY SMLCAP 100', 'NIFTY SMALLCAP 100'],
  ['NIFTY IT', 'NIFTY IT'],
  ['INDIA VIX', 'INDIA VIX'],
];

function padTo(arr: number[], len: number): number[] {
  if (arr.length >= len) return arr.slice(arr.length - len);
  return [...new Array(len - arr.length).fill(0), ...arr];
}

/** Cap on watchlist size — a swing book, not a screener dump. */
const MAX_WATCHLIST = 200;

/** NSE tickers are uppercase alphanumerics plus `&`, `-` and `.` (e.g. M&M, BAJAJ-AUTO). */
const SYMBOL_RE = /^[A-Z0-9&.-]{1,24}$/;

function cleanSymbol(raw: string): string | null {
  const sym = raw.trim().toUpperCase();
  return SYMBOL_RE.test(sym) ? sym : null;
}

async function watchlistFor(userId: string): Promise<string[]> {
  const db = await getDb();
  const rows = await db.all<{ symbol: string }>(
    'SELECT symbol FROM user_watchlist WHERE user_id = ? ORDER BY symbol', [userId],
  );
  return rows.map(r => r.symbol);
}

async function buildSummary(): Promise<Record<string, unknown> | null> {
  const db = await getDb();
  const bRows = await db.all<{ date: string; data: string }>(
    'SELECT date, data FROM breadth_daily ORDER BY date DESC LIMIT 1',
  );
  if (!bRows[0]) return null;
  const date = bRows[0].date;
  const breadth = JSON.parse(bRows[0].data);

  const stockRows = await db.all<{ data: string }>('SELECT data FROM stock_metrics WHERE date = ?', [date]);
  const stocks = stockRows.map(r => JSON.parse(r.data));

  const sectorRows = await db.all<{ data: string }>('SELECT data FROM sector_scores WHERE date = ?', [date]);
  const sectors = sectorRows.map(r => JSON.parse(r.data)).sort((a, b) => b.score - a.score);

  // Index strip: last 32 closes per index. Every price Pulse serves is an
  // end-of-day close — there is no intraday feed behind any of this.
  const indices: { name: string; value: number; chgPct: number; pts: number[] }[] = [];
  for (const [display, fileName] of INDEX_STRIP) {
    const rows = await db.all<{ date: string; close: number }>(
      'SELECT date, close FROM index_bars WHERE index_name = ? ORDER BY date DESC LIMIT 33', [fileName],
    );
    if (!rows.length) continue;
    const pts = rows.map(r => r.close).reverse();
    const value = pts[pts.length - 1];
    const prev = pts.length > 1 ? pts[pts.length - 2] : value;
    indices.push({
      name: display, value,
      chgPct: prev > 0 ? (value / prev - 1) * 100 : 0,
      pts: pts.slice(-32),
    });
  }

  const flowRows = await db.all<{ date: string; category: string; net: number }>(
    'SELECT date, category, net FROM fii_dii ORDER BY date DESC LIMIT 60',
  );
  const fii: number[] = [], dii: number[] = [];
  const flowDates: string[] = [];
  for (const r of flowRows.reverse()) {
    (r.category === 'FII' ? fii : dii).push(r.net);
    if (r.category === 'FII') flowDates.push(r.date);
  }

  const total = breadth.advances + breadth.declines || 1;
  return {
    date,
    stocks: stocks.map(s => ({
      sym: s.sym, sector: s.sector, price: s.price, chg1d: s.chg1d, chg1w: s.chg1w,
      distATH: s.distATH, isATH: s.isATH, is52: s.is52, wkBreak: s.wkBreak,
      athSince: s.athSince ?? null,
    })),
    sectors,
    universe: breadth.universe,
    advances: breadth.advances, declines: breadth.declines, unchanged: breadth.unchanged,
    newHighs: breadth.newHighs, newLows: breadth.newLows, athCount: breadth.athCount,
    avgBias: (breadth.advances - breadth.declines) / total,
    emaVals: breadth.emaVals,
    emaHist: breadth.emaHist,
    adDaily: padTo(breadth.adDaily, 90),
    nhDaily: padTo(breadth.nhDaily, 90),
    series: breadth.series,
    volUp: breadth.series.up4vol[breadth.series.up4vol.length - 1] ?? 0,
    volDn: breadth.series.down4vol[breadth.series.down4vol.length - 1] ?? 0,
    indices,
    flows: { fii: padTo(fii, 20), dii: padTo(dii, 20) },
    // Session date axis for the breadth history, so every chart can state the
    // exact window it covers instead of an unlabelled bar count.
    dates: Array.isArray(breadth.dates) ? breadth.dates : [],
    flowDates: flowDates.slice(-20),
  };
}

export function registerApiRoutes(router: Router): void {
  router.get('/api/market/summary', requireAuth(async ctx => {
    const summary = await buildSummary();
    if (!summary) {
      return json(ctx, 503, {
        error: 'no_data',
        message: 'No published session yet — the nightly pipeline has not run against this database.',
      });
    }
    json(ctx, 200, summary);
  }));

  router.get('/api/stocks/:sym/candles', requireAuth(async ctx => {
    const sym = ctx.params.sym.toUpperCase();
    const n = Math.min(500, Number(ctx.url.searchParams.get('n') ?? 180));
    const db = await getDb();

    // Index names resolve from index_bars (map the smallcap display alias).
    const indexAlias = INDEX_STRIP.find(([d]) => d === sym)?.[1];
    if (indexAlias) {
      const rows = await db.all<{ date: string; open: number; high: number; low: number; close: number }>(
        'SELECT date, open, high, low, close FROM index_bars WHERE index_name = ? ORDER BY date DESC LIMIT ?',
        [indexAlias, n],
      );
      return json(ctx, 200, {
        sym, candles: rows.reverse().map(r => ({ d: r.date, o: r.open, h: r.high, l: r.low, c: r.close, v: 0 })),
      });
    }

    /*
     * Candles come pre-adjusted from the cache the EOD job writes. The 20-year
     * bar history lives on R2 as Parquet — too large for this database, and a
     * columnar scan is the wrong shape for a request that wants one symbol's
     * recent tail.
     */
    const rows = await db.all<{ data: string }>(
      'SELECT data FROM stock_candles WHERE symbol = ?', [sym],
    );
    if (!rows.length) return json(ctx, 404, { error: 'unknown_symbol' });
    const c = JSON.parse(rows[0].data) as {
      d: string[]; o: number[]; h: number[]; l: number[]; c: number[]; v: number[];
    };
    const from = Math.max(0, c.d.length - n);
    const candles = c.d.slice(from).map((d, i) => ({
      d, o: c.o[from + i], h: c.h[from + i], l: c.l[from + i], c: c.c[from + i], v: c.v[from + i],
    }));
    json(ctx, 200, { sym, candles });
  }));

  router.get('/api/watchlist', requireAuth(async ctx => {
    json(ctx, 200, { symbols: await watchlistFor(ctx.session!.userId) });
  }));

  router.post('/api/watchlist/:sym', requireAuth(async ctx => {
    const sym = cleanSymbol(ctx.params.sym);
    if (!sym) return json(ctx, 400, { error: 'bad_symbol' });
    const userId = ctx.session!.userId;
    const db = await getDb();
    const current = await watchlistFor(userId);
    if (current.length >= MAX_WATCHLIST && !current.includes(sym)) {
      return json(ctx, 409, { error: 'watchlist_full', limit: MAX_WATCHLIST });
    }
    await db.run(
      `INSERT INTO user_watchlist (user_id, symbol, added_at) VALUES (?, ?, ?)
       ON CONFLICT(user_id, symbol) DO NOTHING`,
      [userId, sym, new Date().toISOString()],
    );
    json(ctx, 200, { symbols: await watchlistFor(userId) });
  }));

  router.del('/api/watchlist/:sym', requireAuth(async ctx => {
    const sym = cleanSymbol(ctx.params.sym);
    if (!sym) return json(ctx, 400, { error: 'bad_symbol' });
    const userId = ctx.session!.userId;
    const db = await getDb();
    await db.run('DELETE FROM user_watchlist WHERE user_id = ? AND symbol = ?', [userId, sym]);
    json(ctx, 200, { symbols: await watchlistFor(userId) });
  }));

  /**
   * One-shot import of a browser-local watchlist from before watchlists were
   * server-side. Additive — it never removes anything already on the account.
   */
  router.post('/api/watchlist/import', requireAuth(async ctx => {
    const body = await readJson<{ symbols?: unknown }>(ctx);
    const raw = Array.isArray(body?.symbols) ? body.symbols : [];
    const symbols = [...new Set(
      raw.filter((s): s is string => typeof s === 'string').map(cleanSymbol).filter((s): s is string => !!s),
    )];
    const userId = ctx.session!.userId;
    const db = await getDb();
    const now = new Date().toISOString();
    const existing = await watchlistFor(userId);
    const room = Math.max(0, MAX_WATCHLIST - existing.length);
    for (const sym of symbols.slice(0, room)) {
      await db.run(
        `INSERT INTO user_watchlist (user_id, symbol, added_at) VALUES (?, ?, ?)
         ON CONFLICT(user_id, symbol) DO NOTHING`,
        [userId, sym, now],
      );
    }
    json(ctx, 200, { symbols: await watchlistFor(userId) });
  }));

  router.get('/api/prefs', requireAuth(async ctx => {
    const db = await getDb();
    const rows = await db.all<{ data: string }>(
      'SELECT data FROM user_prefs WHERE user_id = ?', [ctx.session!.userId],
    );
    let prefs: unknown = null;
    if (rows[0]) { try { prefs = JSON.parse(rows[0].data); } catch { prefs = null; } }
    json(ctx, 200, { prefs });
  }));

  router.post('/api/prefs', requireAuth(async ctx => {
    const body = await readJson<Record<string, unknown>>(ctx);
    if (!body || typeof body !== 'object') return json(ctx, 400, { error: 'bad_body' });
    // Only the known numeric fields are stored — this blob is echoed back to the
    // client, so it must never become a place to park arbitrary content.
    const num = (v: unknown, lo: number, hi: number, dflt: number) =>
      typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt;
    const prefs = {
      capital: num(body.capital, 0, 1e12, 1000000),
      riskPct: num(body.riskPct, 0, 100, 1),
      maxPos: Math.round(num(body.maxPos, 1, 100, 6)),
    };
    const db = await getDb();
    await db.run(
      `INSERT INTO user_prefs (user_id, data, updated_at) VALUES (?, ?, ?)
       ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at`,
      [ctx.session!.userId, JSON.stringify(prefs), new Date().toISOString()],
    );
    json(ctx, 200, { prefs });
  }));

  router.get('/api/news', requireAuth(async ctx => {
    if (!newsEnabled()) return json(ctx, 200, { enabled: false, articles: [] });

    // Symbols come from the account, not the query string: what a client asks
    // for must not be able to spend the shared Marketaux budget on symbols
    // nobody is actually watching.
    const userId = ctx.session!.userId;
    const symbols = await watchlistFor(userId);
    if (symbols.length === 0) return json(ctx, 200, { enabled: true, articles: [] });

    // Top up whatever has gone stale (at most one Marketaux request), then read
    // the cache. A cold symbol sorts first, so a first view is never empty.
    await refreshIfStale(symbols);
    const articles = await getCachedNews(symbols);
    json(ctx, 200, { enabled: true, articles });
  }));

  router.get('/api/status', requireAuth(async ctx => {
    const db = await getDb();
    // The bar history is on R2, so coverage here is what this database actually
    // serves: the published per-session metrics.
    const bars = (await db.all<{ c: number; days: number }>(
      'SELECT COUNT(*) AS c, COUNT(DISTINCT date) AS days FROM stock_metrics'))[0];
    const log = await db.all(
      'SELECT ts, job, date, status, detail FROM ingest_log ORDER BY ts DESC LIMIT 25',
    );
    json(ctx, 200, {
      marketOpen: isMarketOpen(),
      lastIngestedSession: await metaGet('last_ingested_session'),
      lastAnalyticsDate: await metaGet('last_analytics_date'),
      bars,
      recentLog: log,
    });
  }));
}
