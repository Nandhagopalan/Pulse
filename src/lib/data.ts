/**
 * The shape of a published session, as `/api/market/summary` returns it.
 *
 * This file used to also carry `buildData()`, a seeded-RNG generator that
 * produced a whole fake market — 100-odd symbols with prices, breadth and
 * sector scores — for the "continue with demo data" path. It was removed for
 * the same reason the fabricated fundamentals were: plausible market numbers
 * with no source behind them are dangerous in front of someone deciding what to
 * trade, and a badge in the corner is not enough to undo that.
 *
 * With it gone, every number the terminal displays came from the NSE archives.
 */

export interface Stock {
  sym: string; sector: string; price: number;
  chg1d: number; chg1w: number; distATH: number;
  isATH: boolean; is52: boolean; wkBreak: boolean;
  /** First session of stored history — how far back "all-time" actually reaches. */
  athSince?: string | null;
}

export interface Sector {
  name: string; count: number; adv: number; dec: number;
  dmaPct: number; newHighs: number; wk: number; score: number;
}

export interface IndexSeries {
  name: string; value: number; chgPct: number; pts: number[];
}

export interface MarketData {
  stocks: Stock[];
  sectors: Sector[];
  universe: number;
  advances: number; declines: number; unchanged: number;
  newHighs: number; newLows: number; athCount: number;
  avgBias: number;
  emaVals: { e10: number; e20: number; e50: number; e200: number };
  emaHist: { e20: number[]; e50: number[]; e200: number[] };
  adDaily: number[]; nhDaily: number[];
  series: {
    newHighs: number[]; newLows: number[]; up20: number[]; up30: number[];
    up4vol: number[]; down4vol: number[]; netHL: number[];
  };
  volUp: number; volDn: number;
  indices: IndexSeries[];
  flows: { fii: number[]; dii: number[] }; // ₹ Cr, last 20 sessions
  // Session dates (YYYY-MM-DD) aligned to the tail of the breadth history.
  // Charts read their own window off the end of this axis.
  dates: string[];
  flowDates: string[];
}
