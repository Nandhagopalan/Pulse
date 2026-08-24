/** Terminal-facing API. All routes require an authenticated session. */
import { getDb, metaGet, type Db } from './db.ts';
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
      // Only the symbols with a live trendline break carry these, so spreading
      // the row would ship six nulls for every one of the ~2,800 that do not.
      ...(s.trendBreak ? {
        trendBreak: true, breakDate: s.breakDate, breakWeeks: s.breakWeeks,
        breakLevel: s.breakLevel, breakVol: s.breakVol ?? null,
        trendWeeks: s.trendWeeks, trendTouches: s.trendTouches,
      } : {}),
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

  /*
   * The strategy engine's paper book. Read-only: the book is advanced by the
   * nightly pipeline, never by a request, which is what keeps the record a test
   * of the rules rather than of whoever had the tab open.
   */
  router.get('/api/strategy/summary', requireAuth(async ctx => {
    const db = await getDb();
    const bookId = ctx.url.searchParams.get('book') ?? 'balanced';

    /*
     * The strategy tables arrive with their own migration, and the frontend can
     * deploy before Supabase has applied it. Missing tables are therefore an
     * expected transient state, not a fault: answer with something the tab can
     * render rather than a 500 and a stack trace in the log.
     */
    if (!(await hasStrategyTables(db))) {
      return json(ctx, 503, {
        error: 'not_deployed',
        message: STRATEGY_NOT_DEPLOYED,
      });
    }

    const book = (await db.all<{
      id: string; fill_mode: string; config: string; config_version: number; capital: number;
      started_on: string | null;
    }>(
      'SELECT id, fill_mode, config, config_version, capital, started_on FROM strategy_books WHERE id = ?',
      [bookId],
    ))[0];
    if (!book) {
      return json(ctx, 404, {
        error: 'no_book',
        message: `No book named "${bookId}". Run \`python -m pipeline strategy\` to create it.`,
      });
    }

    const latest = (await db.all<Record<string, unknown>>(
      'SELECT * FROM strategy_state WHERE book_id = ? ORDER BY date DESC LIMIT 1', [bookId]))[0];

    const [equity, positions, signals, closed, books] = await Promise.all([
      db.all<{ date: string; equity: number; deployed: number; regime_on: boolean; twr_factor: number }>(
        `SELECT date, equity, deployed, regime_on, twr_factor FROM strategy_state
          WHERE book_id = ? ORDER BY date`, [bookId]),
      db.all(
        `SELECT id, symbol, sector, entry_date, entry_px, qty, init_stop, stop, r_per_share,
                last_px, bars, pending_exit, config_version, origin
           FROM strategy_positions
          WHERE book_id = ? AND status = 'open'
          ORDER BY entry_date, symbol`, [bookId]),
      // Only the newest session's signals: older pending rows were never taken.
      db.all(
        `SELECT symbol, rank, ref_close, stop, stop_pct, atr, rs_pct, sector, turnover_20d,
                qty, position_value, risk_amount, status, skip_reason
           FROM strategy_signals
          WHERE book_id = ? AND date = (SELECT MAX(date) FROM strategy_signals WHERE book_id = ?)
          ORDER BY rank`, [bookId, bookId]),
      db.all(
        `SELECT symbol, sector, entry_date, entry_px, exit_date, exit_px, qty, bars,
                exit_reason, pnl, r_multiple, origin
           FROM strategy_positions
          WHERE book_id = ? AND status = 'closed'
          ORDER BY exit_date DESC LIMIT 100`, [bookId]),
      db.all<{ id: string }>('SELECT id FROM strategy_books WHERE enabled ORDER BY id'),
    ]);

    /*
     * Equity, deployment and the open count are recomputed from cash plus the
     * open positions rather than served from the stored row. On the manual book
     * a position can be added or closed between nightly runs, which moves cash
     * immediately; the stored row would stay stale until the next run and the
     * tab would not add up.
     *
     * `n_open` belongs in here for the same reason as the other two, and was
     * once left out of it: the book showed a position's cash and deployment
     * while the count beside them still read zero.
     */
    const marked = positions.reduce(
      (sum, p) => sum + Number(p.qty) * Number(p.last_px ?? p.entry_px), 0);
    const liveState = latest
      ? { ...latest, equity: Number(latest.cash) + marked,
          n_open: positions.length,
          deployed: Number(latest.cash) + marked > 0
            ? marked / (Number(latest.cash) + marked) : 0 }
      : null;

    json(ctx, 200, {
      book: {
        id: book.id,
        fillMode: book.fill_mode,
        configVersion: book.config_version,
        capital: book.capital,
        startedOn: book.started_on,
        config: safeParse(book.config),
      },
      books: books.map(b => b.id),
      state: liveState,
      // Chain-linked from the daily factors, so a deposit into the book never
      // reads as performance. Straight equity growth would.
      performance: summarisePaper(equity, Number(book.capital)),
      equity,
      positions,
      signals,
      closed,
    });
  }));

  /*
   * Manual book writes.
   *
   * Guarded on fill_mode: the rules book is filled and closed by the pipeline
   * alone, and letting a request touch it would turn its record into a mixture
   * of the strategy and the operator's judgement with no way to separate them
   * afterwards. That book is the experiment; this one is the operator's.
   */
  router.post('/api/strategy/positions', requireAuth(async ctx => {
    const body = await readJson<Record<string, unknown>>(ctx);
    if (!body) return json(ctx, 400, { error: 'bad_json' });
    const db = await getDb();
    if (!(await hasStrategyTables(db))) {
      return json(ctx, 503, { error: 'not_deployed', message: STRATEGY_NOT_DEPLOYED });
    }

    const bookId = String(body.book ?? 'manual');
    const guard = await requireManualBook(db, bookId);
    if (guard) return json(ctx, guard.status, guard.body);

    const symbol = String(body.symbol ?? '').trim().toUpperCase();
    const entryPx = Number(body.entry_px);
    const stop = Number(body.stop);
    const qty = Math.floor(Number(body.qty));
    const entryDate = String(body.entry_date ?? '').slice(0, 10);
    /*
     * The mark is the operator's own, defaulting to the entry price.
     * Without it a position added between nightly runs sits at its entry until
     * the next one, reporting 0 P&L and 0R on a trade that has already moved —
     * and on a backdated entry that is not a lag, it is wrong.
     */
    const lastPx = body.last_px == null || body.last_px === '' ? entryPx : Number(body.last_px);

    if (!symbol) return json(ctx, 400, { error: 'bad_symbol', message: 'Symbol is required.' });
    if (!Number.isFinite(entryPx) || entryPx <= 0) {
      return json(ctx, 400, { error: 'bad_entry', message: 'Entry price must be above zero.' });
    }
    if (!Number.isFinite(stop) || stop <= 0 || stop >= entryPx) {
      return json(ctx, 400, {
        error: 'bad_stop',
        message: 'Stop must be above zero and below the entry price.',
      });
    }
    if (!Number.isInteger(qty) || qty <= 0) {
      return json(ctx, 400, { error: 'bad_qty', message: 'Quantity must be a whole number above zero.' });
    }
    if (!Number.isFinite(lastPx) || lastPx <= 0) {
      return json(ctx, 400, { error: 'bad_last', message: 'Last price must be above zero.' });
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(entryDate)) {
      return json(ctx, 400, { error: 'bad_date', message: 'Entry date must be YYYY-MM-DD.' });
    }

    const dup = await db.all(
      `SELECT id FROM strategy_positions WHERE book_id = ? AND symbol = ? AND status = 'open'`,
      [bookId, symbol],
    );
    if (dup.length) {
      return json(ctx, 409, {
        error: 'already_open',
        message: `${symbol} is already open in this book. Close it before adding another.`,
      });
    }

    const version = (await db.all<{ config_version: number }>(
      'SELECT config_version FROM strategy_books WHERE id = ?', [bookId]))[0]?.config_version ?? 1;

    /*
     * Buying costs money. The nightly job reads cash off the newest
     * strategy_state row, so a position added between runs has to debit that
     * row — otherwise the book counts the shares *and* the cash that bought
     * them, and equity is overstated by the whole position.
     */
    const outlay = entryPx * qty * (1 + BUY_CHARGES);
    const latest = (await db.all<{ date: string; cash: number }>(
      'SELECT date, cash FROM strategy_state WHERE book_id = ? ORDER BY date DESC LIMIT 1',
      [bookId]))[0];
    if (!latest) {
      return json(ctx, 409, {
        error: 'no_session',
        message: 'This book has no session yet — run the pipeline once before adding positions.',
      });
    }
    if (Number(latest.cash) < outlay) {
      return json(ctx, 409, {
        error: 'insufficient_cash',
        message: `Not enough cash: the position costs ₹${Math.round(outlay).toLocaleString('en-IN')} `
          + `and the book holds ₹${Math.round(Number(latest.cash)).toLocaleString('en-IN')}.`,
      });
    }

    /*
     * One statement, so the position and the cash debit cannot come apart. The
     * pool hands out a connection per call, so two `run`s are two transactions:
     * the first attempt at this left a position with its cash never deducted
     * when the second failed. `deployed` is recomputed by the next nightly run,
     * and is NOT NULL, so it is left alone here.
     */
    /*
     * Sessions held, counted from the entry date rather than started at zero.
     * `bars` is what the time stop is measured in, so a trade entered ten
     * sessions ago and recorded today is ten sessions into its clock, not none
     * of it — starting it at zero would hand a backdated position a fresh
     * sixty-session lease. The nightly run increments it from here.
     */
    const bars = await sessionsBetween(db, entryDate, latest.date);

    await db.run(
      `WITH inserted AS (
         INSERT INTO strategy_positions
           (book_id, config_version, origin, symbol, sector, entry_date, entry_px, qty,
            init_stop, stop, r_per_share, last_px, bars, stale, status)
         VALUES (?,?,'manual',?,?,?,?,?,?,?,?,?,?,0,'open')
         RETURNING book_id
       )
       UPDATE strategy_state SET cash = cash - ?
        WHERE book_id = (SELECT book_id FROM inserted) AND date = ?`,
      [bookId, version, symbol, body.sector ?? null, entryDate, entryPx, qty,
       stop, stop, entryPx - stop, lastPx, bars, outlay, latest.date],
    );
    json(ctx, 201, { ok: true });
  }));

  router.post('/api/strategy/positions/:id/close', requireAuth(async ctx => {
    const body = await readJson<Record<string, unknown>>(ctx);
    if (!body) return json(ctx, 400, { error: 'bad_json' });
    const db = await getDb();
    if (!(await hasStrategyTables(db))) {
      return json(ctx, 503, { error: 'not_deployed', message: STRATEGY_NOT_DEPLOYED });
    }

    const id = Number(ctx.params.id);
    const pos = (await db.all<{
      book_id: string; entry_px: number; qty: number; r_per_share: number; bars: number;
    }>(`SELECT book_id, entry_px, qty, r_per_share, bars FROM strategy_positions
         WHERE id = ? AND status = 'open'`, [id]))[0];
    if (!pos) return json(ctx, 404, { error: 'no_position', message: 'No open position with that id.' });

    const guard = await requireManualBook(db, pos.book_id);
    if (guard) return json(ctx, guard.status, guard.body);

    const exitPx = Number(body.exit_px);
    const exitDate = String(body.exit_date ?? '').slice(0, 10);
    if (!Number.isFinite(exitPx) || exitPx <= 0) {
      return json(ctx, 400, { error: 'bad_exit', message: 'Exit price must be above zero.' });
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(exitDate)) {
      return json(ctx, 400, { error: 'bad_date', message: 'Exit date must be YYYY-MM-DD.' });
    }

    /*
     * Costs match the backtest's model, so a manual trade's P&L is comparable
     * with the rules book's rather than flattered by ignoring charges.
     */
    const cost = pos.entry_px * (1 + BUY_CHARGES) * pos.qty;
    const proceeds = exitPx * (1 - SELL_CHARGES) * pos.qty;
    const pnl = proceeds - cost;
    const risk = pos.r_per_share * pos.qty;

    /*
     * Closing and the cash credit go together, for the same reason as opening.
     *
     * `bars` is settled here too. It is counted by the nightly run, so a
     * position opened and closed by hand between two runs would close having
     * held zero sessions. The market's calendar knows better: count the
     * sessions the trade actually spanned.
     */
    await db.run(
      `WITH closed AS (
         UPDATE strategy_positions
            SET status='closed', exit_date=?, exit_px=?, exit_reason=?, pnl=?, r_multiple=?,
                pending_exit=NULL,
                bars = GREATEST(bars, (
                  SELECT COUNT(DISTINCT b.date) FROM index_bars b
                   WHERE b.date > strategy_positions.entry_date AND b.date <= ?))
          WHERE id = ?
          RETURNING book_id
       )
       UPDATE strategy_state SET cash = cash + ?
        WHERE book_id = (SELECT book_id FROM closed)
          AND date = (SELECT MAX(date) FROM strategy_state WHERE book_id = (SELECT book_id FROM closed))`,
      [exitDate, exitPx, String(body.reason ?? 'manual').slice(0, 32), pnl,
       risk > 0 ? pnl / risk : 0, exitDate, id, proceeds],
    );
    json(ctx, 200, { ok: true, pnl });
  }));

  /*
   * Edit an open manual position.
   *
   * A manual book is a record of what the operator actually did, so it has to
   * be correctable: a fat-fingered entry, a stop moved up during the day, a
   * mark that is newer than last night's close. Everything the nightly run
   * derives — P&L, R, held — follows from these five fields, which is why
   * they are editable together rather than through five little endpoints.
   *
   * Omitted fields keep their current value, so the caller can send just the
   * mark without restating the trade.
   */
  router.post('/api/strategy/positions/:id/edit', requireAuth(async ctx => {
    const body = await readJson<Record<string, unknown>>(ctx);
    if (!body) return json(ctx, 400, { error: 'bad_json' });
    const db = await getDb();
    if (!(await hasStrategyTables(db))) {
      return json(ctx, 503, { error: 'not_deployed', message: STRATEGY_NOT_DEPLOYED });
    }

    const id = Number(ctx.params.id);
    const pos = (await db.all<{
      book_id: string; entry_date: string; entry_px: number; qty: number;
      init_stop: number; stop: number; last_px: number | null;
    }>(`SELECT book_id, entry_date, entry_px, qty, init_stop, stop, last_px
          FROM strategy_positions WHERE id = ? AND status = 'open'`, [id]))[0];
    if (!pos) return json(ctx, 404, { error: 'no_position', message: 'No open position with that id.' });

    const guard = await requireManualBook(db, pos.book_id);
    if (guard) return json(ctx, guard.status, guard.body);

    const keep = (v: unknown, current: number) =>
      v == null || v === '' ? current : Number(v);
    const entryPx = keep(body.entry_px, pos.entry_px);
    const stop = keep(body.stop, pos.stop);
    const lastPx = keep(body.last_px, pos.last_px ?? pos.entry_px);
    const qty = Math.floor(keep(body.qty, pos.qty));
    const entryDate = String(body.entry_date ?? pos.entry_date).slice(0, 10);

    if (!Number.isFinite(entryPx) || entryPx <= 0) {
      return json(ctx, 400, { error: 'bad_entry', message: 'Entry price must be above zero.' });
    }
    if (!Number.isFinite(stop) || stop <= 0 || stop >= entryPx) {
      return json(ctx, 400, {
        error: 'bad_stop',
        message: 'Stop must be above zero and below the entry price.',
      });
    }
    if (!Number.isFinite(lastPx) || lastPx <= 0) {
      return json(ctx, 400, { error: 'bad_last', message: 'Last price must be above zero.' });
    }
    if (!Number.isInteger(qty) || qty <= 0) {
      return json(ctx, 400, { error: 'bad_qty', message: 'Quantity must be a whole number above zero.' });
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(entryDate)) {
      return json(ctx, 400, { error: 'bad_date', message: 'Entry date must be YYYY-MM-DD.' });
    }

    /*
     * Which stop defines R. Stops only ever ratchet up, so a stop moved *up* is
     * a trailing stop and the R baseline it was sized against has to stay put,
     * or every R multiple in the book silently rebases. A stop moved *down*
     * cannot be a ratchet, so it is a correction to the original, and the
     * baseline moves with it.
     */
    const initStop = stop < pos.init_stop ? stop : pos.init_stop;
    const rPerShare = entryPx - initStop;
    if (!(rPerShare > 0)) {
      return json(ctx, 400, {
        error: 'bad_stop',
        message: `The initial stop (${pos.init_stop}) is not below the entry price. `
          + 'Lower the stop to reset it.',
      });
    }

    /*
     * Cash follows the correction. The outlay was debited when the position was
     * opened, so re-pricing or re-sizing it has to settle the difference on the
     * newest state row — the same row the nightly run reads — otherwise the
     * book counts shares it did not pay for.
     */
    const delta = (entryPx * qty - pos.entry_px * pos.qty) * (1 + BUY_CHARGES);
    const latest = (await db.all<{ date: string; cash: number }>(
      'SELECT date, cash FROM strategy_state WHERE book_id = ? ORDER BY date DESC LIMIT 1',
      [pos.book_id]))[0];
    if (!latest) {
      return json(ctx, 409, {
        error: 'no_session',
        message: 'This book has no session yet.',
      });
    }
    if (delta > 0 && Number(latest.cash) < delta) {
      return json(ctx, 409, {
        error: 'insufficient_cash',
        message: `The change costs another ₹${Math.round(delta).toLocaleString('en-IN')} `
          + `and the book holds ₹${Math.round(Number(latest.cash)).toLocaleString('en-IN')}.`,
      });
    }

    /*
     * Sessions held are re-derived, not preserved. On this book the entry date
     * is the fact and `bars` is a count from it, so correcting the date has to
     * move the count with it — and a position added before this was computed
     * on insert is repaired by opening the dialog and saving.
     */
    const bars = await sessionsBetween(db, entryDate, latest.date);

    await db.run(
      `WITH edited AS (
         UPDATE strategy_positions
            SET entry_date=?, entry_px=?, qty=?, init_stop=?, stop=?, r_per_share=?,
                last_px=?, bars=?
          WHERE id = ?
          RETURNING book_id
       )
       UPDATE strategy_state SET cash = cash - ?
        WHERE book_id = (SELECT book_id FROM edited) AND date = ?`,
      [entryDate, entryPx, qty, initStop, stop, rPerShare, lastPx, bars, id, delta, latest.date],
    );
    json(ctx, 200, { ok: true });
  }));

  /*
   * Reset every book to a new opening capital.
   *
   * Destructive on purpose. A track record is a record *of a starting capital*:
   * position sizes, drawdowns and returns were all computed against it, so
   * changing it retroactively would leave a history that never happened. The
   * only honest options are to keep the capital or to start over, and this is
   * start over.
   */
  router.post('/api/strategy/reset', requireAuth(async ctx => {
    const body = await readJson<Record<string, unknown>>(ctx);
    if (!body) return json(ctx, 400, { error: 'bad_json' });
    const db = await getDb();
    if (!(await hasStrategyTables(db))) {
      return json(ctx, 503, { error: 'not_deployed', message: STRATEGY_NOT_DEPLOYED });
    }

    const capital = Number(body.capital);
    if (!Number.isFinite(capital) || capital < 10_000 || capital > 1e10) {
      return json(ctx, 400, {
        error: 'bad_capital',
        message: 'Capital must be between ₹10,000 and ₹1,000 crore.',
      });
    }
    if (body.confirm !== true) {
      return json(ctx, 400, {
        error: 'not_confirmed',
        message: 'Resetting clears every position and all history. Send confirm: true to proceed.',
      });
    }

    const today = new Date().toISOString().slice(0, 10);

    /*
     * Wipe the book, keep the market.
     *
     * Deleting every strategy_state row is what a reset used to do, and it left
     * the tab blank: equity, cash, deployed and the regime banner are all read
     * from the newest row, so with no row at all the page reported a flat book
     * in a bear market. Neither was true — the market had not changed, the book
     * had merely lost the row that reported it.
     *
     * So the history goes and one row stays: the latest session, re-based to
     * the new capital and carrying that session's regime, index and universe
     * forward untouched. Those are facts about the market, not about the book.
     * It also becomes the state the next nightly run resumes from, which is
     * exactly a book that has just started flat.
     *
     * One statement, because a half-reset book is worse than either end of it.
     * The delete and the insert see the same snapshot, so the seed rows are not
     * visible to the delete that precedes them; the signals delete and the
     * signals update touch disjoint rows by construction.
     */
    await db.run(
      `WITH ctx AS (
         SELECT DISTINCT ON (book_id)
                book_id, date, regime_on, ew_index, ew_ma, universe_n
           FROM strategy_state ORDER BY book_id, date DESC
       ),
       wipe_positions AS (DELETE FROM strategy_positions),
       wipe_cashflows AS (DELETE FROM strategy_cashflows),
       -- The seed row is re-based in place rather than deleted and re-inserted:
       -- sub-statements share a snapshot, but the unique index does not, so an
       -- insert in the same command still collides with the row the delete has
       -- only just removed. Everything else about the row is a market fact and
       -- is left exactly as the pipeline recorded it.
       wipe_state AS (
         DELETE FROM strategy_state s
          WHERE NOT EXISTS (
            SELECT 1 FROM ctx WHERE ctx.book_id = s.book_id AND ctx.date = s.date)
       ),
       seed AS (
         UPDATE strategy_state s
            SET equity = ?, cash = ?, deployed = 0, n_open = 0,
                net_flow = 0, twr_factor = 1
           FROM ctx
          WHERE ctx.book_id = s.book_id AND ctx.date = s.date
       ),
       -- Candidates older than the seed session were never taken and never
       -- will be; the seed session's own list is kept and re-sized below.
       wipe_signals AS (
         DELETE FROM strategy_signals s
          WHERE NOT EXISTS (
            SELECT 1 FROM ctx WHERE ctx.book_id = s.book_id AND ctx.date = s.date)
       ),
       /*
        * Re-size the surviving candidates against the new capital. Quantity is
        * a function of equity, so leaving it would advertise a Rs 24k risk on a
        * book that can no longer carry it — and the prefill button on the
        * manual book would hand the operator that number.
        *
        * This mirrors rules.position_size, which remains the definition: the
        * stored quantity is for display and audit only, because book.advance
        * re-derives it from the live equity at the moment of the fill. The next
        * nightly run overwrites these rows outright.
        */
       resize AS (
         UPDATE strategy_signals s
            SET qty            = q.n,
                position_value = q.n * s.ref_close,
                risk_amount    = q.n * (s.ref_close - s.stop)
           FROM (
             SELECT s2.book_id, s2.date, s2.symbol,
                    GREATEST(0, LEAST(
                      floor(COALESCE((b.config::json->>'risk_pct')::float8, 0.006)
                            * ? / (s2.ref_close - s2.stop)),
                      floor(COALESCE((b.config::json->>'max_weight')::float8, 0.10)
                            * ? / s2.ref_close),
                      -- LEAST skips NULLs, so a missing turnover drops the
                      -- liquidity cap rather than zeroing the position.
                      floor(COALESCE((b.config::json->>'adv_cap')::float8, 0.05)
                            * NULLIF(s2.turnover_20d, 0) / s2.ref_close),
                      floor(? / (s2.ref_close
                            * (1 + COALESCE((b.config::json->>'buy_charges')::float8, 0.00147))))
                    ))::int AS n
               FROM strategy_signals s2
               JOIN strategy_books  b  ON b.id = s2.book_id
               JOIN ctx ON ctx.book_id = s2.book_id AND ctx.date = s2.date
              WHERE s2.ref_close > s2.stop
           ) q
          WHERE q.book_id = s.book_id AND q.date = s.date AND q.symbol = s.symbol
       )
       UPDATE strategy_books b
          SET capital    = ?,
              -- The book restarts at the seeded session, so that is when it
              -- started. NULL here was a bug: nothing ever set it again.
              started_on = COALESCE((SELECT date FROM ctx WHERE ctx.book_id = b.id), ?),
              updated_at = ?`,
      [capital, capital, capital, capital, capital, capital, today, today],
    );
    json(ctx, 200, { ok: true, capital });
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

function safeParse(blob: string): unknown {
  try { return JSON.parse(blob); } catch { return null; }
}

/*
 * Headline numbers for the paper book.
 *
 * Returns are chain-linked from the per-session factors the pipeline stores
 * (`twr_factor`), not read off the equity curve. The two agree only while no
 * money moves in or out; the moment capital is added, an equity-derived CAGR
 * counts the deposit as a gain.
 */
function summarisePaper(
  rows: { date: string; equity: number; twr_factor: number }[],
  capital: number,
): Record<string, number | null> {
  if (!rows.length) return { days: 0, totalReturn: null, cagr: null, maxDrawdown: null };

  let growth = 1;
  for (const r of rows) growth *= Number(r.twr_factor) || 1;

  const first = new Date(rows[0].date).getTime();
  const last = new Date(rows[rows.length - 1].date).getTime();
  const years = (last - first) / (365.25 * 24 * 3600 * 1000);

  let peak = -Infinity;
  let maxDd = 0;
  for (const r of rows) {
    peak = Math.max(peak, Number(r.equity));
    maxDd = Math.min(maxDd, Number(r.equity) / peak - 1);
  }

  return {
    days: rows.length,
    equity: Number(rows[rows.length - 1].equity),
    capital,
    totalReturn: growth - 1,
    // A few weeks of data annualises to nonsense, so it is withheld rather than
    // shown as a headline number nobody should act on.
    cagr: years >= 0.5 ? growth ** (1 / years) - 1 : null,
    maxDrawdown: maxDd,
  };
}

/*
 * Cheap existence probe, cached after the first success.
 *
 * Catching 42P01 (undefined_table) around each query instead would mean five
 * separate failure paths and a half-populated response; one check up front
 * keeps the route's happy path clean.
 */
let strategyTablesReady = false;
async function hasStrategyTables(db: Db): Promise<boolean> {
  if (strategyTablesReady) return true;
  const rows = await db.all<{ n: string | number }>(
    `SELECT COUNT(*) AS n FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = 'strategy_books'`,
  );
  strategyTablesReady = Number(rows[0]?.n ?? 0) > 0;
  return strategyTablesReady;
}

const STRATEGY_NOT_DEPLOYED =
  'The strategy engine is not set up on this database yet — its migration has not been applied.';

// Same figures the backtest deducts, so manual and rules P&L stay comparable.
const BUY_CHARGES = 0.00147;
const SELL_CHARGES = 0.00137;

/**
 * Trading sessions strictly after `from`, up to and including `to`.
 *
 * `index_bars` holds one row per index per session, so its distinct dates are
 * the market's own calendar — weekends and holidays are absent because no bar
 * was printed. A book's `strategy_state` rows are NOT a substitute: a book that
 * started yesterday has one of them, and a position backdated a fortnight would
 * report a single session held.
 */
async function sessionsBetween(db: Db, from: string, to: string): Promise<number> {
  const row = (await db.all<{ n: number | string }>(
    'SELECT COUNT(DISTINCT date) AS n FROM index_bars WHERE date > ? AND date <= ?',
    [from, to]))[0];
  return Number(row?.n ?? 0);
}

/** Refuse writes to anything but a manual book. Returns null when allowed. */
async function requireManualBook(
  db: Db, bookId: string,
): Promise<{ status: number; body: Record<string, string> } | null> {
  const row = (await db.all<{ fill_mode: string }>(
    'SELECT fill_mode FROM strategy_books WHERE id = ?', [bookId]))[0];
  if (!row) {
    return { status: 404, body: { error: 'no_book', message: `No book named "${bookId}".` } };
  }
  if (row.fill_mode !== 'manual') {
    return {
      status: 403,
      body: {
        error: 'read_only',
        message: `"${bookId}" is filled by the pipeline and cannot be edited by hand. `
          + 'Use the manual book, so the strategy\'s own record stays a clean test of the rules.',
      },
    };
  }
  return null;
}
