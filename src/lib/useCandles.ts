import { useEffect, useState } from 'react';
import { ohlc, type Candle } from './candles';
import { fetchCandles, type ApiCandle } from './api';

/** Below this the series is too short to read an EMA off, so prefer the sample. */
const MIN_REAL = 20;

export interface CandleSeries {
  candles: Candle[];
  /** ISO session dates aligned with `candles` — null while the series is synthetic. */
  dates: string[] | null;
  real: boolean;
}

/**
 * One symbol's recent daily candles, real where the backend has them.
 *
 * Both the Charts tab and the detail drawer draw the same instrument, so they
 * have to agree about what it did. The drawer used to skip the fetch and chart
 * `ohlc()` alone, which meant opening a stock from search showed a seeded
 * random walk next to its real last price — and the position plan sized itself
 * off that invented 10-day low.
 */
export function useCandles(sym: string, n: number, fallbackLast: number, fallbackVol: number): CandleSeries {
  const [real, setReal] = useState<{ sym: string; candles: ApiCandle[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchCandles(sym, n)
      .then(r => { if (!cancelled && r.candles.length >= MIN_REAL) setReal(r); })
      .catch(() => { /* fall back to synthetic candles */ });
    return () => { cancelled = true; };
  }, [sym, n]);

  // A resolved fetch for the previous symbol must never paint under the new one.
  const hit = real?.sym === sym ? real.candles : null;
  if (!hit) return { candles: ohlc(sym, fallbackLast, fallbackVol, n), dates: null, real: false };
  return {
    candles: hit.map(c => ({ o: c.o, c: c.c, h: c.h, l: c.l })),
    dates: hit.map(c => c.d),
    real: true,
  };
}
