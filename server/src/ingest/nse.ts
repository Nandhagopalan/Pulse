/**
 * Official NSE EOD file ingestion (per the sourcing spec):
 *  - CM-UDiFF Common Bhavcopy  BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
 *  - Security Deliverable file MTO_DDMMYYYY.DAT
 *  - Index close file          ind_close_all_DDMMYYYY.csv
 *  - Index constituent lists   ind_nifty500list.csv etc. (sector/industry mapping)
 *  - FII/DII provisional flows (best effort — endpoint is frequently geo/bot-blocked)
 *
 * Corporate actions are detected automatically: when the official previous close
 * for a session diverges from our stored close of the prior session, the ratio is
 * recorded as an adjustment factor k (adjusted = raw / k for all bars before ex-date).
 */
import { unzipSync } from 'fflate';
import { getDb, logIngest, metaSet } from '../db.ts';
import { fetchBytes, parseCsv, csvObjects, compact, ddmmyyyy, addDays, isWeekend, istToday, sleep } from '../util.ts';

const ARCHIVES = 'https://nsearchives.nseindia.com';

const INDEX_LISTS: Record<string, string> = {
  'NIFTY 50': '/content/indices/ind_nifty50list.csv',
  'NIFTY 500': '/content/indices/ind_nifty500list.csv',
  'NIFTY MIDCAP 100': '/content/indices/ind_niftymidcap100list.csv',
  'NIFTY SMLCAP 100': '/content/indices/ind_niftysmallcap100list.csv',
  'NIFTY BANK': '/content/indices/ind_niftybanklist.csv',
  'NIFTY IT': '/content/indices/ind_niftyitlist.csv',
};

const num = (s: string | undefined): number => {
  const n = parseFloat(s ?? '');
  return Number.isFinite(n) ? n : 0;
};

/**
 * NSE replaced the legacy bhavcopy with the CM-UDiFF format in 2024; the legacy
 * archive covers everything before it. Sessions on/after this date use UDiFF.
 */
const UDIFF_FROM = '2024-01-01';

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

/** Legacy archive path: /2020/AUG/cm06AUG2020bhav.csv.zip */
function legacyBhavUrl(date: string): string {
  const [y, m, d] = date.split('-');
  const mon = MONTHS[Number(m) - 1];
  return `${ARCHIVES}/content/historical/EQUITIES/${y}/${mon}/cm${d}${mon}${y}bhav.csv.zip`;
}

/**
 * Normalise one bhavcopy row to the fields we store. The two formats use entirely
 * different column names for the same data, so map both to a single shape.
 */
function bhavRow(r: Record<string, string>, udiff: boolean) {
  return udiff
    ? { sym: r.tckrsymb, series: r.sctysrs, isin: r.isin,
        open: r.opnpric, high: r.hghpric, low: r.lwpric, close: r.clspric,
        prev: r.prvsclsgpric, vol: r.ttltradgvol, val: r.ttltrfval, trades: r.ttlnboftxsexctd }
    : { sym: r.symbol, series: r.series, isin: r.isin,
        open: r.open, high: r.high, low: r.low, close: r.close,
        prev: r.prevclose, vol: r.tottrdqty, val: r.tottrdval, trades: r.totaltrades };
}

/** Ingest the bhavcopy for one session. Returns rows ingested (0 = holiday/missing). */
export async function ingestBhavcopy(date: string): Promise<number> {
  const udiff = date >= UDIFF_FROM;
  const url = udiff
    ? `${ARCHIVES}/content/cm/BhavCopy_NSE_CM_0_0_0_${compact(date)}_F_0000.csv.zip`
    : legacyBhavUrl(date);
  const buf = await fetchBytes(url);
  if (!buf) { await logIngest('bhavcopy', date, 'missing'); return 0; }

  const files = unzipSync(new Uint8Array(buf));
  const csvName = Object.keys(files).find(n => n.toLowerCase().endsWith('.csv'));
  if (!csvName) throw new Error('bhavcopy zip had no csv');
  const rows = csvObjects(parseCsv(Buffer.from(files[csvName]).toString('utf8')));

  const db = await getDb();
  await db.exec('BEGIN');
  let count = 0;
  try {
    for (const raw of rows) {
      const r = bhavRow(raw, udiff);
      const series = r.series;
      if (series !== 'EQ' && series !== 'BE' && series !== 'BZ') continue;
      const sym = r.sym;
      if (!sym) continue;
      const close = num(r.close);
      const officialPrev = num(r.prev);
      if (close <= 0) continue; // spec: null/zero close → treat as halted, keep last bar

      await db.run(
        `INSERT INTO daily_bars (symbol, date, open, high, low, close, prev_close, volume, traded_value, trades, delivery_pct)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
         ON CONFLICT(symbol, date) DO UPDATE SET
           open = excluded.open, high = excluded.high, low = excluded.low, close = excluded.close,
           prev_close = excluded.prev_close, volume = excluded.volume,
           traded_value = excluded.traded_value, trades = excluded.trades`,
        [sym, date, num(r.open), num(r.high), num(r.low), close, officialPrev,
         num(r.vol), num(r.val), num(r.trades)],
      );

      // Backfill walks backwards through history: don't let an old session
      // resurrect a delisted symbol as active, or overwrite a newer ISIN.
      await db.run(
        `INSERT INTO instruments (symbol, isin, series, active) VALUES (?, ?, ?, 1)
         ON CONFLICT(symbol) DO UPDATE SET isin = COALESCE(instruments.isin, excluded.isin),
           series = COALESCE(instruments.series, excluded.series)`,
        [sym, r.isin ?? null, series],
      );

      count++;
    }
    await db.exec('COMMIT');
  } catch (err) {
    await db.exec('ROLLBACK');
    throw err;
  }
  await logIngest('bhavcopy', date, 'ok', `${count} rows`);
  return count;
}

/** Ingest the security-wise delivery report (MTO) for one session. */
export async function ingestDelivery(date: string): Promise<number> {
  const buf = await fetchBytes(`${ARCHIVES}/archives/equities/mto/MTO_${ddmmyyyy(date)}.DAT`);
  if (!buf) { await logIngest('mto', date, 'missing'); return 0; }
  const db = await getDb();
  let count = 0;
  await db.exec('BEGIN');
  try {
    for (const line of buf.toString('utf8').split('\n')) {
      const f = line.split(',').map(s => s.trim());
      // Record type 20: <20, srno, symbol, series, traded_qty, deliverable_qty, pct>
      if (f[0] !== '20' || f.length < 7) continue;
      if (f[3] !== 'EQ' && f[3] !== 'BE' && f[3] !== 'BZ') continue;
      await db.run('UPDATE daily_bars SET delivery_pct = ? WHERE symbol = ? AND date = ?', [num(f[6]), f[2], date]);
      count++;
    }
    await db.exec('COMMIT');
  } catch (err) {
    await db.exec('ROLLBACK');
    throw err;
  }
  await logIngest('mto', date, 'ok', `${count} rows`);
  return count;
}

/** Ingest the all-indices close file for one session (index OHLC history). */
export async function ingestIndexClose(date: string): Promise<number> {
  const buf = await fetchBytes(`${ARCHIVES}/content/indices/ind_close_all_${ddmmyyyy(date)}.csv`);
  if (!buf) { await logIngest('index_close', date, 'missing'); return 0; }
  const rows = csvObjects(parseCsv(buf.toString('utf8')));
  const db = await getDb();
  let count = 0;
  await db.exec('BEGIN');
  try {
    for (const r of rows) {
      const name = (r.index_name ?? '').toUpperCase();
      const close = num(r.closing_index_value);
      if (!name || close <= 0) continue;
      await db.run(
        `INSERT INTO index_bars (index_name, date, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT(index_name, date) DO UPDATE SET
           open = excluded.open, high = excluded.high, low = excluded.low, close = excluded.close`,
        [name, date, num(r.open_index_value), num(r.high_index_value), num(r.low_index_value), close],
      );
      count++;
    }
    await db.exec('COMMIT');
  } catch (err) {
    await db.exec('ROLLBACK');
    throw err;
  }
  await logIngest('index_close', date, 'ok', `${count} indices`);
  return count;
}

/** Refresh index constituent lists → index_membership + instrument industry/sector. */
export async function ingestIndexConstituents(): Promise<void> {
  const db = await getDb();
  for (const [indexName, path] of Object.entries(INDEX_LISTS)) {
    const buf = await fetchBytes(ARCHIVES + path);
    if (!buf) { await logIngest('constituents', indexName, 'missing'); continue; }
    const rows = csvObjects(parseCsv(buf.toString('utf8')));
    await db.run('DELETE FROM index_membership WHERE index_name = ?', [indexName]);
    for (const r of rows) {
      const sym = r.symbol;
      if (!sym) continue;
      await db.run('INSERT INTO index_membership (index_name, symbol) VALUES (?, ?) ON CONFLICT DO NOTHING', [indexName, sym]);
      const industry = r.industry ?? '';
      if (industry) {
        await db.run(
          `INSERT INTO instruments (symbol, name, industry, sector, active) VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(symbol) DO UPDATE SET name = COALESCE(excluded.name, instruments.name),
             industry = excluded.industry, sector = excluded.sector`,
          [sym, r.company_name ?? null, industry, industry],
        );
      }
    }
    await logIngest('constituents', indexName, 'ok', `${rows.length} symbols`);
    await sleep(300);
  }
}

/** Best-effort FII/DII provisional flows (main NSE site — often blocked for bots). */
export async function ingestFiiDii(): Promise<void> {
  try {
    const res = await fetch('https://www.nseindia.com/api/fiidiiTradeReact', {
      headers: {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://www.nseindia.com/reports/fii-dii',
      },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error('status ' + res.status);
    const data = await res.json() as { category: string; date: string; buyValue: string; sellValue: string; netValue: string }[];
    const db = await getDb();
    for (const r of data) {
      const cat = r.category.toUpperCase().includes('FII') || r.category.toUpperCase().includes('FPI') ? 'FII' : 'DII';
      // date arrives as DD-Mon-YYYY
      const d = new Date(r.date + ' UTC');
      const iso = Number.isNaN(d.getTime()) ? istToday() : d.toISOString().slice(0, 10);
      await db.run(
        `INSERT INTO fii_dii (date, category, buy, sell, net) VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(date, category) DO UPDATE SET buy = excluded.buy, sell = excluded.sell, net = excluded.net`,
        [iso, cat, num(r.buyValue), num(r.sellValue), num(r.netValue)],
      );
    }
    await logIngest('fii_dii', istToday(), 'ok');
  } catch (err) {
    await logIngest('fii_dii', istToday(), 'failed', String(err));
  }
}

/** Most recent session date having a bhavcopy, scanning back from today. */
export async function latestAvailableSession(): Promise<string | null> {
  let d = istToday();
  for (let i = 0; i < 10; i++) {
    if (!isWeekend(d)) {
      const url = `${ARCHIVES}/content/cm/BhavCopy_NSE_CM_0_0_0_${compact(d)}_F_0000.csv.zip`;
      const res = await fetch(url, {
        method: 'HEAD',
        headers: { 'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)' },
        signal: AbortSignal.timeout(15000),
      });
      if (res.ok) return d;
    }
    d = addDays(d, -1);
  }
  return null;
}

export interface BackfillProgress {
  running: boolean;
  done: number;
  target: number;
  currentDate: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
}

export const backfillProgress: BackfillProgress = {
  running: false, done: 0, target: 0, currentDate: null, startedAt: null, finishedAt: null, error: null,
};

/**
 * Bootstrap / deepening backfill: ensure `sessions` bhavcopies of history exist.
 *
 * Walks back from the oldest session already stored (or from the latest available
 * session on an empty DB), so re-running with a larger `sessions` extends history
 * instead of re-fetching what we already have. Holidays are skipped. Index closes
 * are pulled per session; the delivery file only for the most recent few, since
 * MTO archives thin out and delivery % is only used for the latest session.
 */
export async function backfill(sessions: number): Promise<void> {
  if (backfillProgress.running) return;

  const db = await getDb();
  const have = await db.all<{ sessions: number; oldest: string | null }>(
    'SELECT COUNT(DISTINCT date) AS sessions, MIN(date) AS oldest FROM daily_bars',
  );
  const totalSessions = have[0]?.sessions ?? 0;

  /*
   * Resume below the oldest session of the NEWEST contiguous run, not below
   * MIN(date). History can be non-contiguous — a shallow initial backfill leaves
   * a recent block, and deepening from MIN(date) would extend an old tail while
   * the hole in the middle stays open. A gap also breaks corporate-action
   * detection, which infers splits by comparing each close against the previous
   * stored row: across a multi-year hole an ordinary price move looks like a split.
   */
  const allDates = (await db.all<{ date: string }>('SELECT DISTINCT date FROM daily_bars ORDER BY date DESC'))
    .map(r => r.date);
  let oldest: string | null = allDates[0] ?? null;
  let contiguous = allDates.length ? 1 : 0;
  for (let i = 1; i < allDates.length; i++) {
    const gapDays = (Date.parse(allDates[i - 1]) - Date.parse(allDates[i])) / 86_400_000;
    if (gapDays > 10) break; // holiday runs stay well under this; a real hole does not
    oldest = allDates[i];
    contiguous++;
  }
  // Only the contiguous run counts toward the target: sessions stranded on the
  // far side of a hole would otherwise end the walk before the hole is filled.
  const haveSessions = contiguous;
  if (totalSessions > contiguous) {
    console.log(`[backfill] ${totalSessions - contiguous} session(s) sit beyond a gap; filling from ${oldest} down`);
  }

  Object.assign(backfillProgress, {
    running: true, done: haveSessions, target: sessions, currentDate: null,
    startedAt: new Date().toISOString(), finishedAt: null, error: null,
  });
  try {
    await ingestIndexConstituents();

    // Empty DB → start at the newest session. Existing DB → deepen below the oldest.
    let latest: string | null = null;
    let d: string;
    if (oldest && haveSessions > 0) {
      d = addDays(oldest, -1);
    } else {
      latest = await latestAvailableSession();
      if (!latest) throw new Error('could not locate any recent bhavcopy');
      d = latest;
    }

    if (haveSessions >= sessions) {
      console.log(`[backfill] already have ${haveSessions} sessions (target ${sessions}) — nothing to do`);
      backfillProgress.finishedAt = new Date().toISOString();
      return;
    }

    let got = haveSessions, scanned = 0;
    const toFetch = sessions - haveSessions;
    while (got < sessions && scanned < toFetch * 2 + 60) {
      if (!isWeekend(d)) {
        backfillProgress.currentDate = d;
        try {
          const n = await ingestBhavcopy(d);
          if (n > 0) {
            await ingestIndexClose(d);
            if (latest && got < 5) await ingestDelivery(d);
            got++;
            backfillProgress.done = got;
          }
        } catch (err) {
          console.error('[backfill]', d, err);
          await logIngest('bhavcopy', d, 'error', String(err));
        }
        await sleep(150);
      }
      scanned++;
      d = addDays(d, -1);
    }
    if (latest) await metaSet('last_ingested_session', latest);
    backfillProgress.finishedAt = new Date().toISOString();
  } catch (err) {
    backfillProgress.error = String(err);
    throw err;
  } finally {
    backfillProgress.running = false;
  }
}

/** Full EOD chain for one session (Phase 4/5 of the daily schedule). */
export async function runEodIngest(date: string): Promise<boolean> {
  const n = await ingestBhavcopy(date);
  if (n === 0) return false;
  await ingestDelivery(date);
  await ingestIndexClose(date);
  await ingestFiiDii();
  await metaSet('last_ingested_session', date);
  return true;
}
