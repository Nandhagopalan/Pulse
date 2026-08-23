import { useEffect, useMemo } from 'react';
import { T, dirColor } from '../theme';
import { Mono } from './ui';
import { stockTag, breakSummary } from './StockTable';
import type { Stock } from '../lib/data';
import { candleChart } from '../lib/candles';
import { useCandles } from '../lib/useCandles';
import { fmtPct, fmtPrice } from '../lib/format';

export function StockDrawer({ stock, watch, toggle, onClose }: {
  stock: Stock;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const { candles, real } = useCandles(stock.sym, 60, stock.price, 2.2);
  const chart = useMemo(() => candleChart(candles, 380, 150, 6), [candles]);

  const starred = !!watch[stock.sym];

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(35,43,56,0.28)', zIndex: 50, animation: 'fade-in 140ms ease' }} />
      <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 440, maxWidth: '100vw', background: T.card, boxShadow: '-12px 0 40px rgba(15,23,42,0.10)', zIndex: 51, overflowY: 'auto', overflowX: 'hidden', padding: 'clamp(16px, 4vw, 26px)', paddingBottom: 32, animation: 'drawer-in 180ms ease' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            {/* Wraps because a long symbol next to two tags overruns the 440px drawer. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: T.serif, fontSize: 24, fontWeight: 600, color: T.ink }}>{stock.sym}</span>
              {stockTag(stock)}
            </div>
            <div style={{ fontSize: 12.5, color: T.muted, marginTop: 3 }}>{stock.sector} · NSE</div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => toggle(stock.sym)} title={starred ? 'Remove from watchlist' : 'Add to watchlist'} style={{ appearance: 'none', border: '1px solid ' + T.border, background: starred ? T.amberSoft : T.card, cursor: 'pointer', fontSize: 15, color: starred ? T.amber : T.faint, borderRadius: 8, padding: '4px 10px', lineHeight: 1.3 }}>{starred ? '★' : '☆'}</button>
            <button onClick={onClose} title="Close" style={{ appearance: 'none', border: '1px solid ' + T.border, background: T.card, cursor: 'pointer', fontSize: 15, color: T.muted, borderRadius: 8, padding: '4px 10px', lineHeight: 1.3 }}>✕</button>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginTop: 14, flexWrap: 'wrap' }}>
          <Mono size={24} weight={600}>{fmtPrice(stock.price)}</Mono>
          <Mono size={13} color={dirColor(stock.chg1d)} weight={600}>{fmtPct(stock.chg1d)} 1D</Mono>
          <Mono size={13} color={dirColor(stock.chg1w)} weight={600}>{fmtPct(stock.chg1w)} 1W</Mono>
        </div>
        <div style={{ fontSize: 12, color: T.faint, marginTop: 4 }}>
          {stock.distATH < 0.05 ? 'Trading at its all-time high' : stock.distATH.toFixed(1) + '% below all-time high'}
          {stock.athSince && <span> · since {stock.athSince.slice(0, 4)}</span>}
        </div>

        {stock.trendBreak && (
          <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 10, background: T.brand50, fontSize: 12.5, color: T.text, lineHeight: 1.5 }}>
            {breakSummary(stock)}
          </div>
        )}

        <svg viewBox="0 0 380 150" preserveAspectRatio="none" style={{ width: '100%', height: 150, marginTop: 16, display: 'block', overflow: 'visible' }}>
          <path d="M0 38 H380 M0 75 H380 M0 112 H380" stroke={T.borderSoft} strokeWidth={1} />
          <path d={chart.ema50} fill="none" stroke={T.navy} strokeWidth={1.3} opacity={0.75} />
          <path d={chart.ema10} fill="none" stroke={T.amber} strokeWidth={1.3} opacity={0.85} />
          <path d={chart.uw} fill={T.up} />
          <path d={chart.dw} fill={T.down} />
          <path d={chart.ub} fill={T.up} />
          <path d={chart.db} fill={T.down} />
        </svg>
        <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 11, color: T.muted, flexWrap: 'wrap' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 12, height: 2, background: T.amber }} />10 EMA {chart.ema10Last}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 12, height: 2, background: T.navy }} />50 EMA {chart.ema50Last}</span>
          <span style={{ color: T.faint }}>{candles.length} sessions · {real ? 'NSE EOD (adjusted)' : 'sample'}</span>
        </div>

      </div>
    </>
  );
}
