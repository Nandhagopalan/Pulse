import { mulberryRng } from './candles';

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

const SECTORS: [string, number, string[]][] = [
  ['Defence', 0.9, ['HAL', 'BEL', 'MAZDOCK', 'BDL', 'COCHINSHIP', 'DATAPATTNS', 'BEML', 'GRSE']],
  ['Capital Goods', 0.75, ['LT', 'SIEMENS', 'ABB', 'CUMMINSIND', 'THERMAX', 'POLYCAB', 'HAVELLS', 'CGPOWER', 'BHEL', 'SUZLON']],
  ['Auto', 0.6, ['MARUTI', 'TATAMOTORS', 'M&M', 'BAJAJ-AUTO', 'EICHERMOT', 'HEROMOTOCO', 'TVSMOTOR', 'ASHOKLEY', 'BHARATFORG', 'MOTHERSON']],
  ['Fin Services', 0.55, ['BAJFINANCE', 'BAJAJFINSV', 'HDFCAMC', 'SBILIFE', 'HDFCLIFE', 'ICICIGI', 'MUTHOOTFIN', 'CHOLAFIN', 'ANGELONE', 'LICHSGFIN']],
  ['Banks', 0.45, ['HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK', 'BANKBARODA', 'PNB', 'CANBK', 'FEDERALBNK', 'AUBANK']],
  ['Energy', 0.35, ['RELIANCE', 'ONGC', 'NTPC', 'POWERGRID', 'TATAPOWER', 'IOC', 'BPCL', 'GAIL', 'COALINDIA', 'ADANIGREEN']],
  ['Metals', 0.25, ['TATASTEEL', 'JSWSTEEL', 'HINDALCO', 'VEDL', 'JINDALSTEL', 'SAIL', 'NMDC', 'NATIONALUM', 'HINDZINC']],
  ['Chemicals', 0.1, ['PIDILITIND', 'SRF', 'AARTIIND', 'DEEPAKNTR', 'TATACHEM', 'NAVINFLUOR', 'FLUOROCHEM', 'ATUL']],
  ['Pharma', 0.05, ['SUNPHARMA', 'CIPLA', 'DRREDDY', 'DIVISLAB', 'LUPIN', 'AUROPHARMA', 'TORNTPHARM', 'ALKEM', 'ZYDUSLIFE', 'LAURUSLABS']],
  ['Realty', -0.05, ['DLF', 'GODREJPROP', 'OBEROIRLTY', 'PRESTIGE', 'PHOENIXLTD', 'BRIGADE', 'SOBHA', 'LODHA']],
  ['FMCG', -0.2, ['HINDUNILVR', 'ITC', 'NESTLEIND', 'BRITANNIA', 'DABUR', 'MARICO', 'GODREJCP', 'TATACONSUM', 'COLPAL', 'VBL']],
  ['IT', -0.35, ['TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTIM', 'PERSISTENT', 'COFORGE', 'MPHASIS', 'KPITTECH']],
];

const IDX: [string, number, number, number][] = [
  ['NIFTY 50', 24812.35, 0.62, 0.16], ['NIFTY BANK', 55240.10, 0.41, 0.20],
  ['NIFTY MIDCAP 100', 58920.75, 1.08, 0.26], ['NIFTY SMLCAP 100', 18445.60, 1.35, 0.32],
  ['NIFTY IT', 40180.25, -0.54, 0.24], ['INDIA VIX', 12.84, -3.20, 0.8],
];

let cached: MarketData | null = null;

export function buildData(): MarketData {
  if (cached) return cached;
  const rnd = mulberryRng(42);
  const gauss = () => (rnd() + rnd() + rnd() - 1.5) * 2;
  const walk = (n: number, end: number, vol: number, lo: number, hi: number) => {
    const a: number[] = []; let v = end - gauss() * vol * 2;
    for (let i = 0; i < n; i++) { v += gauss() * vol + (end - v) * 0.04; v = Math.max(lo, Math.min(hi, v)); a.push(v); }
    a[n - 1] = end; return a;
  };

  const stocks: Stock[] = [];
  const sectors: Sector[] = [];
  let sumBias = 0;

  for (const [name, bias, syms] of SECTORS) {
    sumBias += bias;
    for (const sym of syms) {
      const q = rnd();
      const chg1d = gauss() * 1.3 + bias * 0.6;
      const chg1w = gauss() * 2.8 + bias * 2.4;
      let distATH = Math.pow(rnd(), 2.2) * (34 - bias * 18) - bias * 1.5;
      if (distATH < 0) distATH = 0;
      const isATH = distATH < 0.4;
      const dist52 = isATH ? 0 : Math.min(distATH, Math.pow(rnd(), 2) * 12);
      const is52 = isATH || dist52 < 0.5;
      const wkBreak = !is52 && chg1w > 3.5 && dist52 < 6;
      const price = q < 0.3 ? 80 + rnd() * 400 : q < 0.75 ? 400 + rnd() * 1800 : 2000 + rnd() * 6000;
      stocks.push({ sym, sector: name, price, chg1d, chg1w, distATH, isATH, is52, wkBreak });
    }
    const noise = gauss() * 4;
    const dmaPct = Math.max(8, Math.min(96, Math.round(50 + bias * 42 + noise)));
    const count = Math.round(140 + rnd() * 380);
    const advShare = Math.max(0.18, Math.min(0.9, 0.5 + bias * 0.3 + gauss() * 0.04));
    const adv = Math.round(count * advShare);
    const newHighs = Math.round(count * Math.max(0, 0.015 + bias * 0.075 + gauss() * 0.006));
    const wk = bias * 2.4 + gauss() * 0.8;
    const score = Math.max(2, Math.min(99, Math.round(dmaPct * 0.5 + Math.max(0, Math.min(30, (wk + 3) * 5)) + Math.min(20, newHighs / count * 220))));
    sectors.push({ name, count, adv, dec: count - adv, dmaPct, newHighs, wk, score });
  }
  sectors.sort((a, b) => b.score - a.score);

  const avgBias = sumBias / SECTORS.length;
  const universe = 5214;
  const advances = Math.round(universe * (0.5 + avgBias * 0.13));
  const unchanged = Math.round(universe * 0.055);
  const declines = universe - advances - unchanged;
  const newHighs = sectors.reduce((s, x) => s + x.newHighs, 0);
  const newLows = Math.round(newHighs * 0.32);
  const athCount = Math.round(newHighs * 0.38);

  const emaVals = {
    e10: Math.max(12, Math.min(90, Math.round(52 + avgBias * 40))),
    e20: Math.max(12, Math.min(90, Math.round(50 + avgBias * 38))),
    e50: Math.max(12, Math.min(90, Math.round(48 + avgBias * 34))),
    e200: Math.max(12, Math.min(90, Math.round(52 + avgBias * 28))),
  };
  const emaHist = {
    e20: walk(120, emaVals.e20, 3.5, 8, 92),
    e50: walk(120, emaVals.e50, 3, 8, 92),
    e200: walk(120, emaVals.e200, 2.2, 8, 92),
  };

  const adDaily: number[] = []; const nhDaily: number[] = [];
  for (let i = 0; i < 90; i++) {
    adDaily.push(Math.round((advances - declines) * 0.5 + gauss() * 820));
    nhDaily.push(Math.round(((i / 89) - 0.42) * 1.9 * 300 + gauss() * 70));
  }
  adDaily[89] = advances - declines; nhDaily[89] = newHighs - newLows;

  const mk = (n: number, base: number, vol: number, lo: number, hi: number) => {
    const a: number[] = []; let v = base;
    for (let i = 0; i < n; i++) { v = Math.max(lo, Math.min(hi, v + gauss() * vol)); a.push(Math.round(v)); }
    return a;
  };
  const series = {
    newHighs: mk(45, 55, 16, 8, 100),
    newLows: mk(45, 30, 12, 3, 70),
    up20: mk(45, 24, 12, 2, 62),
    up30: mk(45, 5, 2.6, 0, 13),
    up4vol: mk(45, 110, 55, 5, 320),
    down4vol: mk(45, 55, 40, 3, 185),
    netHL: [] as number[],
  };
  series.netHL = series.newHighs.map((v, i) => v - series.newLows[i]);

  const volUp = series.up4vol[44], volDn = series.down4vol[44];

  const indices: IndexSeries[] = IDX.map(([name, value, chgPct, vol]) => {
    const n = 32, pts: number[] = []; let v = value * (1 - chgPct / 100); const drift = (value - v) / (n - 1);
    for (let i = 0; i < n; i++) { pts.push(v + gauss() * vol * value / 100 * 0.5); v += drift; }
    pts[n - 1] = value;
    return { name, value, chgPct, pts };
  });

  const flows = { fii: [] as number[], dii: [] as number[] };
  for (let i = 0; i < 20; i++) {
    flows.fii.push(Math.round(gauss() * 2400 - 350 + avgBias * 900));
    flows.dii.push(Math.round(gauss() * 1500 + 1150));
  }

  // Weekday-only session axis ending today, matching the 120-session breadth
  // history the analytics engine emits.
  const dates: string[] = [];
  const cursor = new Date();
  while (dates.length < 120) {
    const day = cursor.getDay();
    if (day !== 0 && day !== 6) dates.push(cursor.toISOString().slice(0, 10));
    cursor.setDate(cursor.getDate() - 1);
  }
  dates.reverse();

  cached = {
    stocks, sectors, universe, advances, declines, unchanged, newHighs, newLows, athCount,
    avgBias, emaVals, emaHist, adDaily, nhDaily, series, volUp, volDn, indices, flows,
    dates, flowDates: dates.slice(-20),
  };
  return cached;
}
