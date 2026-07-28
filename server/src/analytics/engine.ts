/**
 * Analytical processing engine (Phase 6 of the daily schedule).
 *
 * Loads the adjusted OHLCV matrix for the active universe into memory and computes,
 * across every session in the window:
 *   - corporate-action detection + adjustment factor chains
 *   - EMAs (10/20/50/200) and % of universe above each EMA (history)
 *   - advances/declines, 52-week new highs/lows, ATH proximity, drawdown buckets
 *   - 5-day 20%/30% movers, ±4% volume shockers (vol > 20-session SMA)
 *   - Mansfield-style relative strength vs NIFTY 500
 *   - sector composite strength scores
 * Results are persisted as JSON snapshots keyed by session date.
 */
import { getDb, logIngest, metaSet } from '../db.ts';

interface SymbolSeries {
  symbol: string;
  sector: string | null;
  // Arrays aligned to the global `dates` axis; NaN where no bar exists.
  close: Float64Array;
  high: Float64Array;
  low: Float64Array;
  volume: Float64Array;
  deliveryPct: number | null;
  lastIdx: number; // last index with a bar
}

const WINDOW = 300; // sessions kept in memory (>= 252 for 52-week stats)

function ema(values: Float64Array, period: number, upto: number): Float64Array {
  const out = new Float64Array(upto + 1).fill(NaN);
  const k = 2 / (period + 1);
  let prev = NaN;
  for (let i = 0; i <= upto; i++) {
    const v = values[i];
    if (Number.isNaN(v)) { out[i] = prev; continue; }
    prev = Number.isNaN(prev) ? v : v * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** Detect corporate actions from stored bars and persist adjustment factors. */
export async function detectCorporateActions(): Promise<number> {
  const db = await getDb();
  const rows = await db.all<{ symbol: string; date: string; close: number; prev_close: number }>(
    'SELECT symbol, date, close, prev_close FROM daily_bars ORDER BY symbol, date',
  );
  let detected = 0;
  await db.exec('BEGIN');
  try {
    await db.run("DELETE FROM corporate_actions WHERE kind = 'detected'", []);
    let prevSym = '', prevClose = 0;
    for (const r of rows) {
      if (r.symbol === prevSym && prevClose > 0 && r.prev_close > 0) {
        const k = prevClose / r.prev_close;
        // >2% divergence between our stored close and the official previous close
        // indicates a split/bonus/rights adjustment on the ex-date.
        if (k > 1.02 || k < 0.98) {
          await db.run(
            `INSERT INTO corporate_actions (symbol, ex_date, kind, factor, detail) VALUES (?, ?, 'detected', ?, ?)
             ON CONFLICT(symbol, ex_date, kind) DO UPDATE SET factor = excluded.factor, detail = excluded.detail`,
            [r.symbol, r.date, k, `stored=${prevClose} official_prev=${r.prev_close}`],
          );
          detected++;
        }
      }
      prevSym = r.symbol;
      prevClose = r.close;
    }
    await db.exec('COMMIT');
  } catch (err) {
    await db.exec('ROLLBACK');
    throw err;
  }
  return detected;
}

/** Cumulative adjustment multiplier per symbol: date → k to divide by for bars before it. */
async function loadFactors(): Promise<Map<string, { exDate: string; factor: number }[]>> {
  const db = await getDb();
  const rows = await db.all<{ symbol: string; ex_date: string; factor: number }>(
    'SELECT symbol, ex_date, factor FROM corporate_actions ORDER BY symbol, ex_date',
  );
  const map = new Map<string, { exDate: string; factor: number }[]>();
  for (const r of rows) {
    // Ignore implausible factors (data glitches): real splits/bonuses are between 20:1 and 1:20.
    if (r.factor <= 0.05 || r.factor >= 20) continue;
    let list = map.get(r.symbol);
    if (!list) { list = []; map.set(r.symbol, list); }
    list.push({ exDate: r.ex_date, factor: r.factor });
  }
  return map;
}

export async function runAnalytics(): Promise<string | null> {
  const t0 = Date.now();
  const db = await getDb();

  const dateRows = await db.all<{ date: string }>(
    `SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ${WINDOW}`,
  );
  const dates = dateRows.map(r => r.date).reverse();
  if (dates.length < 30) return null; // not enough history yet
  const n = dates.length;
  const last = n - 1;
  const latestDate = dates[last];
  const dateIdx = new Map(dates.map((d, i) => [d, i]));

  await detectCorporateActions();
  const factors = await loadFactors();

  const secRows = await db.all<{ symbol: string; sector: string | null }>(
    'SELECT symbol, sector FROM instruments',
  );
  const sectorOf = new Map(secRows.map(r => [r.symbol, r.sector]));

  const bars = await db.all<{
    symbol: string; date: string; high: number; low: number; close: number; volume: number; delivery_pct: number | null;
  }>(
    'SELECT symbol, date, high, low, close, volume, delivery_pct FROM daily_bars WHERE date >= ? ORDER BY symbol, date',
    [dates[0]],
  );

  // ── Assemble adjusted per-symbol series ──────────────────────────────────
  const universe: SymbolSeries[] = [];
  let cur: SymbolSeries | null = null;
  for (const b of bars) {
    if (!cur || cur.symbol !== b.symbol) {
      cur = {
        symbol: b.symbol, sector: sectorOf.get(b.symbol) ?? null,
        close: new Float64Array(n).fill(NaN), high: new Float64Array(n).fill(NaN),
        low: new Float64Array(n).fill(NaN), volume: new Float64Array(n).fill(NaN),
        deliveryPct: null, lastIdx: -1,
      };
      universe.push(cur);
    }
    const i = dateIdx.get(b.date);
    if (i === undefined) continue;
    cur.close[i] = b.close; cur.high[i] = b.high; cur.low[i] = b.low; cur.volume[i] = b.volume;
    if (i > cur.lastIdx) { cur.lastIdx = i; cur.deliveryPct = b.delivery_pct; }
  }

  // Apply corporate-action factor chains (adjusted = raw / k for bars before ex-date).
  for (const s of universe) {
    const acts = factors.get(s.symbol);
    if (!acts?.length) continue;
    for (const a of acts) {
      const exIdx = dateIdx.get(a.exDate) ?? -1;
      const bound = exIdx >= 0 ? exIdx : n; // action after window → adjust everything
      if (a.exDate < dates[0]) continue;    // action before window → already consistent
      for (let i = 0; i < bound; i++) {
        if (!Number.isNaN(s.close[i])) {
          s.close[i] /= a.factor; s.high[i] /= a.factor; s.low[i] /= a.factor;
          s.volume[i] *= a.factor;
        }
      }
    }
  }

  // Active universe = symbols with a bar on the latest session.
  const active = universe.filter(s => s.lastIdx === last && s.close[last] > 0);

  // ── Index series ─────────────────────────────────────────────────────────
  const idxBars = await db.all<{ index_name: string; date: string; close: number }>(
    'SELECT index_name, date, close FROM index_bars WHERE date >= ? ORDER BY index_name, date', [dates[0]],
  );
  const indexSeries = new Map<string, Float64Array>();
  for (const b of idxBars) {
    let arr = indexSeries.get(b.index_name);
    if (!arr) { arr = new Float64Array(n).fill(NaN); indexSeries.set(b.index_name, arr); }
    const i = dateIdx.get(b.date);
    if (i !== undefined) arr[i] = b.close;
  }
  const benchmark = indexSeries.get('NIFTY 500') ?? indexSeries.get('NIFTY 50') ?? null;

  // ── Per-day aggregate accumulators ───────────────────────────────────────
  const HIST = Math.min(120, n - 1); // history depth for breadth series
  const histStart = n - HIST;
  const zeros = () => new Int32Array(HIST);
  const agg = {
    advances: zeros(), declines: zeros(), unchanged: zeros(),
    newHighs: zeros(), newLows: zeros(), athCount: zeros(),
    above10: zeros(), above20: zeros(), above50: zeros(), above200: zeros(), counted: zeros(),
    up20: zeros(), up30: zeros(), volUp4: zeros(), volDn4: zeros(),
  };

  interface StockOut {
    sym: string; sector: string; price: number; chg1d: number; chg1w: number;
    distATH: number; dist52: number; isATH: boolean; is52: boolean; wkBreak: boolean;
    e10: number; e20: number; e50: number; e200: number;
    rs: number; volume: number; deliveryPct: number | null; turnover: number;
  }
  const stocksOut: StockOut[] = [];

  const benchRet63 = benchmark && !Number.isNaN(benchmark[last]) && last >= 63 && !Number.isNaN(benchmark[last - 63])
    ? benchmark[last] / benchmark[last - 63] : null;

  for (const s of active) {
    const c = s.close, h = s.high, v = s.volume;
    const e10 = ema(c, 10, last), e20 = ema(c, 20, last), e50 = ema(c, 50, last), e200 = ema(c, 200, last);

    // rolling 20-session volume SMA (simple loop; universe is small enough)
    for (let j = 0; j < HIST; j++) {
      const i = histStart + j;
      const ci = c[i], pi = c[i - 1];
      if (Number.isNaN(ci) || Number.isNaN(pi) || pi <= 0) continue;
      agg.counted[j]++;
      const chg = (ci - pi) / pi * 100;
      if (chg > 0.0001) agg.advances[j]++; else if (chg < -0.0001) agg.declines[j]++; else agg.unchanged[j]++;

      // 52-week window ending at i
      const from = Math.max(0, i - 251);
      let hi52 = -Infinity, lo52 = Infinity;
      for (let t = from; t <= i; t++) {
        const ht = h[t];
        if (!Number.isNaN(ht)) { if (ht > hi52) hi52 = ht; const lt = s.low[t]; if (lt < lo52) lo52 = lt; }
      }
      if (hi52 > 0 && h[i] >= hi52) agg.newHighs[j]++;
      if (lo52 < Infinity && s.low[i] <= lo52) agg.newLows[j]++;

      if (!Number.isNaN(e10[i]) && ci > e10[i]) agg.above10[j]++;
      if (!Number.isNaN(e20[i]) && ci > e20[i]) agg.above20[j]++;
      if (!Number.isNaN(e50[i]) && ci > e50[i]) agg.above50[j]++;
      if (!Number.isNaN(e200[i]) && ci > e200[i]) agg.above200[j]++;

      if (i >= 5 && !Number.isNaN(c[i - 5]) && c[i - 5] > 0) {
        const r5 = (ci / c[i - 5] - 1) * 100;
        if (r5 >= 20) agg.up20[j]++;
        if (r5 >= 30) agg.up30[j]++;
      }
      if (i >= 20) {
        let sum = 0, cnt = 0;
        for (let t = i - 20; t < i; t++) { const vt = v[t]; if (!Number.isNaN(vt)) { sum += vt; cnt++; } }
        const sma = cnt ? sum / cnt : 0;
        if (sma > 0 && v[i] > sma) {
          if (chg >= 4) agg.volUp4[j]++;
          else if (chg <= -4) agg.volDn4[j]++;
        }
      }
    }

    // ── Latest-session stock metrics ──
    const price = c[last];
    const prev = c[last - 1];
    const chg1d = !Number.isNaN(prev) && prev > 0 ? (price / prev - 1) * 100 : 0;
    const i5 = last - 5;
    const chg1w = i5 >= 0 && !Number.isNaN(c[i5]) && c[i5] > 0 ? (price / c[i5] - 1) * 100 : 0;

    let athHigh = -Infinity, hi52 = -Infinity, lo52 = Infinity;
    const from52 = Math.max(0, last - 251);
    for (let t = 0; t <= last; t++) {
      const ht = h[t];
      if (Number.isNaN(ht)) continue;
      if (ht > athHigh) athHigh = ht;
      if (t >= from52) { if (ht > hi52) hi52 = ht; const lt = s.low[t]; if (lt < lo52) lo52 = lt; }
    }
    const distATH = athHigh > 0 ? Math.max(0, (athHigh - price) / athHigh * 100) : 0;
    const dist52 = hi52 > 0 ? Math.max(0, (hi52 - price) / hi52 * 100) : 0;
    const isATH = distATH < 0.4;
    const is52 = dist52 < 0.5;
    const wkBreak = !is52 && chg1w > 3.5 && dist52 < 6;
    if (isATH) agg.athCount[HIST - 1]++;

    let rs = 0;
    if (benchRet63 && last >= 63 && !Number.isNaN(c[last - 63]) && c[last - 63] > 0) {
      rs = (price / c[last - 63]) / benchRet63 * 100 - 100; // % out/under-performance vs benchmark, 63 sessions
    }

    stocksOut.push({
      sym: s.symbol, sector: s.sector ?? 'Other', price,
      chg1d, chg1w, distATH, dist52, isATH, is52, wkBreak,
      e10: e10[last], e20: e20[last], e50: e50[last], e200: e200[last],
      rs, volume: v[last], deliveryPct: s.deliveryPct, turnover: v[last] * price,
    });
  }

  // ── Sector composites (constituents of mapped sectors only) ─────────────
  const bySector = new Map<string, StockOut[]>();
  for (const st of stocksOut) {
    if (st.sector === 'Other') continue;
    let list = bySector.get(st.sector);
    if (!list) { list = []; bySector.set(st.sector, list); }
    list.push(st);
  }
  const sectors = [...bySector.entries()].map(([name, list]) => {
    const count = list.length;
    const adv = list.filter(x => x.chg1d > 0).length;
    const above50 = list.filter(x => !Number.isNaN(x.e50) && x.price > x.e50).length;
    const dmaPct = Math.round(above50 / count * 100);
    const newHighs = list.filter(x => x.is52).length;
    const wk = list.reduce((sum, x) => sum + x.chg1w, 0) / count;
    const score = Math.max(2, Math.min(99, Math.round(
      dmaPct * 0.5 + Math.max(0, Math.min(30, (wk + 3) * 5)) + Math.min(20, newHighs / count * 220),
    )));
    return { name, count, adv, dec: count - adv, dmaPct, newHighs, wk, score };
  }).sort((a, b) => b.score - a.score);

  // ── Persist snapshots ────────────────────────────────────────────────────
  const j = HIST - 1;
  const pct = (arr: Int32Array, i: number) => agg.counted[i] ? Math.round(arr[i] / agg.counted[i] * 100) : 0;
  const series = (arr: Int32Array, len: number) => Array.from(arr.slice(HIST - Math.min(len, HIST)));

  const breadth = {
    date: latestDate,
    universe: active.length,
    advances: agg.advances[j], declines: agg.declines[j], unchanged: agg.unchanged[j],
    newHighs: agg.newHighs[j], newLows: agg.newLows[j], athCount: agg.athCount[j],
    emaVals: { e10: pct(agg.above10, j), e20: pct(agg.above20, j), e50: pct(agg.above50, j), e200: pct(agg.above200, j) },
    emaHist: {
      e20: Array.from({ length: HIST }, (_, i) => pct(agg.above20, i)),
      e50: Array.from({ length: HIST }, (_, i) => pct(agg.above50, i)),
      e200: Array.from({ length: HIST }, (_, i) => pct(agg.above200, i)),
    },
    adDaily: Array.from({ length: Math.min(90, HIST) }, (_, i) => {
      const t = HIST - Math.min(90, HIST) + i;
      return agg.advances[t] - agg.declines[t];
    }),
    nhDaily: Array.from({ length: Math.min(90, HIST) }, (_, i) => {
      const t = HIST - Math.min(90, HIST) + i;
      return agg.newHighs[t] - agg.newLows[t];
    }),
    series: {
      newHighs: series(agg.newHighs, 45), newLows: series(agg.newLows, 45),
      up20: series(agg.up20, 45), up30: series(agg.up30, 45),
      up4vol: series(agg.volUp4, 45), down4vol: series(agg.volDn4, 45),
      netHL: series(agg.newHighs, 45).map((v, i) => v - series(agg.newLows, 45)[i]),
    },
    dates: dates.slice(histStart),
  };

  await db.exec('BEGIN');
  try {
    await db.run(
      'INSERT INTO breadth_daily (date, data) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET data = excluded.data',
      [latestDate, JSON.stringify(breadth)],
    );
    await db.run('DELETE FROM sector_scores WHERE date = ?', [latestDate]);
    for (const sec of sectors) {
      await db.run('INSERT INTO sector_scores (date, sector, data) VALUES (?, ?, ?)', [latestDate, sec.name, JSON.stringify(sec)]);
    }
    await db.run('DELETE FROM stock_metrics WHERE date = ?', [latestDate]);
    for (const st of stocksOut) {
      await db.run('INSERT INTO stock_metrics (date, symbol, data) VALUES (?, ?, ?)', [latestDate, st.sym, JSON.stringify(st)]);
    }
    await db.exec('COMMIT');
  } catch (err) {
    await db.exec('ROLLBACK');
    throw err;
  }

  await metaSet('last_analytics_date', latestDate);
  await logIngest('analytics', latestDate, 'ok', `${active.length} stocks, ${sectors.length} sectors in ${Date.now() - t0}ms`);
  console.log(`[analytics] ${latestDate}: ${active.length} stocks, ${sectors.length} sectors (${Date.now() - t0}ms)`);
  return latestDate;
}
