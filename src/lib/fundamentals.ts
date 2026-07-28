import { mulberryRng } from './candles';
import { strHash } from './svg';

export interface Fundamentals {
  mcap: number;        // ₹ Cr
  pe: number;
  pb: number;
  roe: number;         // %
  roce: number;        // %
  opm: number;         // %
  salesG: number;      // 3Y CAGR %
  profitG: number;     // 3Y CAGR %
  eps: number;         // ₹
  de: number;          // debt / equity
  promoter: number;    // % holding
  promoterChg: number; // QoQ change in pp
  fii: number;         // % holding
  dii: number;         // % holding
  divYield: number;    // %
  quality: number;     // 0-100 composite
}

// Typical sector profiles: [P/E, ROE, D/E, 3Y growth]
const PROFILE: Record<string, [number, number, number, number]> = {
  'Defence': [52, 20, 0.15, 28],
  'Capital Goods': [46, 19, 0.35, 22],
  'Auto': [28, 17, 0.5, 18],
  'Fin Services': [24, 16, 1.6, 20],
  'Banks': [14, 14, 6.5, 15],
  'Energy': [16, 13, 0.8, 12],
  'Metals': [12, 12, 0.7, 9],
  'Chemicals': [32, 15, 0.4, 11],
  'Pharma': [30, 16, 0.3, 14],
  'Realty': [38, 11, 0.6, 21],
  'FMCG': [48, 24, 0.1, 9],
  'IT': [26, 22, 0.05, 10],
};

const cache = new Map<string, Fundamentals>();

export function fundamentals(sym: string, sector: string, price: number): Fundamentals {
  const hit = cache.get(sym);
  if (hit) return hit;
  const rnd = mulberryRng(strHash('fund:' + sym));
  const g = () => (rnd() + rnd() + rnd() - 1.5) * 2;
  const [peB, roeB, deB, gB] = PROFILE[sector] || [24, 15, 0.6, 14];

  const pe = Math.max(6, peB * (0.7 + rnd() * 0.8));
  const roe = Math.max(2, roeB + g() * 4);
  const roce = sector === 'Banks' || sector === 'Fin Services' ? roe * (0.9 + rnd() * 0.2) : Math.max(3, roe * (1 + rnd() * 0.35));
  const opm = Math.max(4, 14 + g() * 6 + (roeB - 15));
  const salesG = gB + g() * 5;
  const profitG = salesG + g() * 7;
  const eps = price / pe;
  const de = Math.max(0, deB * (0.5 + rnd()));
  const promoter = Math.max(0, Math.min(78, 48 + g() * 12));
  const promoterChg = g() * 0.7;
  const fii = Math.max(1, 14 + g() * 6);
  const dii = Math.max(1, 15 + g() * 5);
  const divYield = Math.max(0, 1.1 - pe / 60 + rnd() * 0.9);
  const mcap = Math.round(price * (2 + Math.pow(rnd(), 1.6) * 120) * (rnd() < 0.25 ? 8 : 1));

  const deNorm = sector === 'Banks' || sector === 'Fin Services' ? 0 : Math.min(20, de * 12);
  const quality = Math.round(Math.max(4, Math.min(98,
    roe * 1.6 + Math.max(0, Math.min(24, profitG)) + Math.max(0, (promoter - 40) * 0.4) - deNorm + (promoterChg > 0 ? 5 : 0),
  )));

  const f: Fundamentals = { mcap, pe, pb: pe * roe / 100, roe, roce, opm, salesG, profitG, eps, de, promoter, promoterChg, fii, dii, divYield, quality };
  cache.set(sym, f);
  return f;
}

export function fmtCr(v: number) {
  if (v >= 100000) return (v / 100000).toFixed(2) + ' L Cr';
  return v.toLocaleString('en-IN') + ' Cr';
}
