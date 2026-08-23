import { ema, strHash } from './svg';

export interface Candle { o: number; c: number; h: number; l: number }

export function mulberryRng(seed: number) {
  let s = seed;
  return () => {
    s |= 0; s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const candleCache = new Map<string, Candle[]>();

export function ohlc(key: string, last: number, volPct: number, n: number): Candle[] {
  // Length belongs in the key: the drawer asks for 60 sessions and the Charts
  // tab for 90, and keying on the symbol alone served whichever ran first.
  const cacheKey = key + ':' + n;
  const cached = candleCache.get(cacheKey);
  if (cached) return cached;
  const rnd = mulberryRng(strHash(key));
  const g = () => (rnd() + rnd() + rnd() - 1.5) * 2;
  let price = last * (1 - (0.05 + rnd() * 0.16));
  const drift = (last - price) / n;
  const candles: Candle[] = [];
  for (let i = 0; i < n; i++) {
    const o = price;
    const step = g() * volPct * price / 100 + drift;
    const c = o + step;
    const wig = Math.abs(g()) * volPct * price / 100 * 0.55;
    const hi = Math.max(o, c) + wig, lo = Math.min(o, c) - Math.abs(g()) * volPct * price / 100 * 0.55;
    candles.push({ o, c, h: hi, l: lo });
    price = c;
  }
  const ratio = last / candles[n - 1].c;
  candles.forEach(cd => { cd.o *= ratio; cd.c *= ratio; cd.h *= ratio; cd.l *= ratio; });
  candleCache.set(cacheKey, candles);
  return candles;
}

export interface CandleChartResult {
  ub: string; db: string; uw: string; dw: string;
  ema10: string; ema50: string;
  priceTicks: string[];
  lastY: string; lastText: string; lastColor: string;
  ema10Last: string; ema20Last: string; ema50Last: string; ema200Last: string;
  emaRaw: { e10: number; e20: number; e50: number; e200: number; last: number };
}

export function candleChart(candles: Candle[], w: number, h: number, pad = 8): CandleChartResult {
  const closes = candles.map(c => c.c);
  const ema10 = ema(closes, 10), ema50 = ema(closes, 50);
  let max = Math.max(...candles.map(c => c.h)), min = Math.min(...candles.map(c => c.l));
  const padR = (max - min) * 0.06 || 1; max += padR; min -= padR;
  const rng = (max - min) || 1;
  const Y = (p: number) => pad + (h - 2 * pad) * (max - p) / rng;
  const step = w / candles.length, bw = Math.min(13, step * 0.62);
  let ub = '', db = '', uw = '', dw = '';
  candles.forEach((c, i) => {
    const xc = i * step + step / 2, x = xc - bw / 2;
    const yt = Y(Math.max(c.o, c.c)), yb = Y(Math.min(c.o, c.c)), bh = Math.max(1, yb - yt);
    const body = 'M' + x.toFixed(1) + ' ' + yt.toFixed(1) + ' h' + bw.toFixed(1) + ' v' + bh.toFixed(1) + ' h' + (-bw).toFixed(1) + ' Z ';
    const wick = 'M' + (xc - 0.7).toFixed(1) + ' ' + Y(c.h).toFixed(1) + ' h1.4 v' + (Y(c.l) - Y(c.h)).toFixed(1) + ' h-1.4 Z ';
    if (c.c >= c.o) { ub += body; uw += wick; } else { db += body; dw += wick; }
  });
  const poly = (arr: number[]) => arr.map((v, i) => (i ? 'L' : 'M') + (i * step + step / 2).toFixed(1) + ' ' + Y(v).toFixed(1)).join(' ');
  const fmt = (v: number) => v.toLocaleString('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const priceTicks = [0, 1, 2, 3, 4].map(k => fmt(max - rng * k / 4));
  const last = closes[closes.length - 1], prev = closes[closes.length - 2];
  return {
    ub, db, uw, dw, ema10: poly(ema10), ema50: poly(ema50), priceTicks,
    lastY: Y(last).toFixed(1), lastText: fmt(last), lastColor: last >= prev ? '#16A34A' : '#E11D48',
    ema10Last: fmt(ema10[ema10.length - 1]), ema20Last: fmt(ema(closes, 20).slice(-1)[0]),
    ema50Last: fmt(ema50[ema50.length - 1]), ema200Last: fmt(ema(closes, 200).slice(-1)[0]),
    emaRaw: { e10: ema10.slice(-1)[0], e20: ema(closes, 20).slice(-1)[0], e50: ema50.slice(-1)[0], e200: ema(closes, 200).slice(-1)[0], last },
  };
}
