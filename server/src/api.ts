/** Terminal-facing API. All routes require an authenticated session. */
import { config } from './config.ts';
import { getDb, metaGet } from './db.ts';
import { requireAuth } from './auth.ts';
import { json, type Router } from './router.ts';
import { backfillProgress } from './ingest/nse.ts';
import { liveQuote, isMarketOpen } from './live.ts';
import { getCachedNews, registerWatched, newsEnabled, fetchNewsBatch } from './news.ts';

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

  // Index strip: last 32 closes per index + live overlay during market hours.
  const indices: { name: string; value: number; chgPct: number; pts: number[] }[] = [];
  for (const [display, fileName] of INDEX_STRIP) {
    const rows = await db.all<{ date: string; close: number }>(
      'SELECT date, close FROM index_bars WHERE index_name = ? ORDER BY date DESC LIMIT 33', [fileName],
    );
    if (!rows.length) continue;
    const pts = rows.map(r => r.close).reverse();
    let value = pts[pts.length - 1];
    let prev = pts.length > 1 ? pts[pts.length - 2] : value;
    const live = liveQuote(display);
    if (live && isMarketOpen()) {
      prev = value;          // last EOD close becomes the reference
      value = live.last;
      pts.push(live.last);
    }
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
        message: backfillProgress.running
          ? `Backfill in progress: ${backfillProgress.done}/${backfillProgress.target} sessions`
          : 'No market data ingested yet. Backfill has not completed.',
        backfill: backfillProgress,
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

    const rows = await db.all<{ date: string; open: number; high: number; low: number; close: number; volume: number }>(
      'SELECT date, open, high, low, close, volume FROM daily_bars WHERE symbol = ? ORDER BY date DESC LIMIT ?',
      [sym, n],
    );
    if (!rows.length) return json(ctx, 404, { error: 'unknown_symbol' });
    const bars = rows.reverse();

    // Apply corporate-action adjustment chain.
    const acts = await db.all<{ ex_date: string; factor: number }>(
      'SELECT ex_date, factor FROM corporate_actions WHERE symbol = ? AND factor > 0.05 AND factor < 20 ORDER BY ex_date',
      [sym],
    );
    const candles = bars.map(b => {
      let k = 1;
      for (const a of acts) if (a.ex_date > b.date) k *= a.factor;
      return { d: b.date, o: b.open / k, h: b.high / k, l: b.low / k, c: b.close / k, v: b.volume * k };
    });
    json(ctx, 200, { sym, candles });
  }));

  router.get('/api/news', requireAuth(async ctx => {
    const raw = (ctx.url.searchParams.get('symbols') ?? '').trim();
    const symbols = [...new Set(
      raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean),
    )].slice(0, 50);

    if (!newsEnabled()) return json(ctx, 200, { enabled: false, articles: [] });
    if (symbols.length === 0) return json(ctx, 200, { enabled: true, articles: [] });

    // Register interest so the 30-min job keeps these warm.
    await registerWatched(symbols);

    let articles = await getCachedNews(symbols);
    // Cold cache (symbol never fetched) — do one on-demand batch so the first
    // view isn't empty, then re-read. Subsequent refreshes come from the job.
    if (articles.length === 0) {
      const fresh = symbols.filter(Boolean);
      if (fresh.length) {
        await fetchNewsBatch(fresh.slice(0, Math.max(1, config.newsSymbolsPerReq)));
        articles = await getCachedNews(symbols);
      }
    }
    json(ctx, 200, { enabled: true, articles });
  }));

  router.get('/api/status', requireAuth(async ctx => {
    const db = await getDb();
    const barCount = await db.all<{ c: number; days: number }>(
      'SELECT COUNT(*) AS c, COUNT(DISTINCT date) AS days FROM daily_bars',
    );
    const log = await db.all(
      'SELECT ts, job, date, status, detail FROM ingest_log ORDER BY ts DESC LIMIT 25',
    );
    json(ctx, 200, {
      marketOpen: isMarketOpen(),
      lastIngestedSession: await metaGet('last_ingested_session'),
      lastAnalyticsDate: await metaGet('last_analytics_date'),
      bars: barCount[0],
      backfill: backfillProgress,
      recentLog: log,
    });
  }));
}
