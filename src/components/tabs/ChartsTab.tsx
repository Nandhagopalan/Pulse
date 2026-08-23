import { useEffect, useState } from 'react';
import { T, dirColor } from '../../theme';
import { Card, Label, Mono } from '../ui';
import type { MarketData } from '../../lib/data';
import { areaLine, barsBottom } from '../../lib/svg';
import { ohlc, candleChart, type Candle } from '../../lib/candles';
import { fetchCandles, type ApiCandle } from '../../lib/api';
import type { Media } from '../../lib/useMedia';

function fmtAxisDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z');
  return d.getUTCDate().toString().padStart(2, '0') + ' ' + d.toLocaleString('en-IN', { month: 'short', timeZone: 'UTC' });
}

export function ChartsTab({ D, chartSym, watch, media, onPickSymbol }: {
  D: MarketData;
  chartSym: string;
  watch: Record<string, true>;
  media: Media;
  /** Opens the palette in chart mode — any symbol, not just indices and stars. */
  onPickSymbol: () => void;
}) {
  const idxSyms = D.indices.map(ix => ({ key: ix.name, label: ix.name, sub: 'Index', last: ix.value, vol: ix.name === 'INDIA VIX' ? 4 : 0.95 }));
  const watchSyms = D.stocks.filter(s => watch[s.sym]).map(s => ({ key: s.sym, label: s.sym, sub: s.sector, last: s.price, vol: 2.2 }));
  const allSyms = [...idxSyms, ...watchSyms];

  // The picker can now reach the whole universe, so resolve the routed symbol
  // against every stock before giving up. Without this, #/charts/TATAPOWER
  // silently charted the first index — the wrong instrument under the right
  // heading.
  const routed = allSyms.some(x => x.key === chartSym)
    ? null
    : D.stocks.find(s => s.sym === chartSym);
  const sel = routed
    ? { key: routed.sym, label: routed.sym, sub: routed.sector, last: routed.price, vol: 2.2 }
    : allSyms.find(x => x.key === chartSym) || allSyms[0];

  const [real, setReal] = useState<{ sym: string; candles: ApiCandle[] } | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchCandles(sel.key, 90)
      .then(r => { if (!cancelled && r.candles.length >= 20) setReal(r); })
      .catch(() => { /* fall back to synthetic candles */ });
    return () => { cancelled = true; };
  }, [sel.key]);

  const realCandles = real?.sym === sel.key ? real.candles : null;
  const baseCandles: Candle[] = realCandles
    ? realCandles.map(c => ({ o: c.o, c: c.c, h: c.h, l: c.l }))
    : ohlc(sel.key, sel.last, sel.vol, 90);

  // Every series ends on the last completed session — Pulse has no intraday
  // feed, so there is no forming candle to append.
  const candles: Candle[] = baseCandles;

  const chart = candleChart(candles, 900, 336, 8);
  const prevC = candles[candles.length - 2].c;
  const dayChg = (sel.last - prevC) / prevC * 100;
  const emaPills = [
    ['10 EMA', chart.ema10Last, chart.emaRaw.e10], ['20 EMA', chart.ema20Last, chart.emaRaw.e20],
    ['50 EMA', chart.ema50Last, chart.emaRaw.e50], ['200 EMA', chart.ema200Last, chart.emaRaw.e200],
  ].map(([label, val, raw]) => {
    const above = chart.emaRaw.last >= (raw as number);
    return { label: label as string, val: val as string, color: above ? T.up : T.down, bg: above ? T.upSoft : T.downSoft };
  });
  const nnhStrip = barsBottom(D.series.newHighs, 900, 40, 4);
  const chartDates = realCandles
    ? [0, 1, 2, 3, 4, 5].map(i => fmtAxisDate(realCandles[Math.min(realCandles.length - 1, Math.round(i * (realCandles.length - 1) / 5))].d))
    : ['24 Apr', '12 May', '30 May', '18 Jun', '07 Jul', '23 Jul'];
  const emaMinis = [
    ['20 EMA', D.emaVals.e20, D.emaHist.e20],
    ['50 EMA', D.emaVals.e50, D.emaHist.e50],
  ].map(([label, value, hist]) => {
    const p = areaLine(hist as number[], 300, 48, 5, 6);
    return { label: label as string, value: (value as number).toFixed(1), line: p.line, area: p.area };
  });

  return (
    <div style={{ marginTop: 18 }}>
      <Card style={{ padding: media.isMobile ? '14px 14px' : '16px 20px', display: 'flex', alignItems: media.isMobile ? 'stretch' : 'center', justifyContent: 'space-between', gap: media.isMobile ? 12 : 20, flexWrap: 'wrap', flexDirection: media.isMobile ? 'column' : 'row' }}>
        <div style={{ display: 'flex', alignItems: media.isMobile ? 'stretch' : 'center', gap: media.isMobile ? 10 : 16, flexDirection: media.isMobile ? 'column' : 'row', minWidth: 0 }}>
          {/* Was a <select> over indices plus starred stocks only, which made
              every other symbol unchartable. It opens the palette instead. */}
          <button
            onClick={onPickSymbol}
            title="Change symbol"
            style={{ appearance: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 9, fontFamily: T.sans, fontSize: 15, fontWeight: 700, padding: '8px 10px 8px 12px', borderRadius: 10, border: '1px solid ' + T.border, background: T.cardAlt, color: T.ink, maxWidth: '100%', minWidth: 0 }}
          >
            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sel.key}</span>
            <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={T.faint} strokeWidth={2} strokeLinecap="round" style={{ flexShrink: 0 }}>
              <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
            </svg>
          </button>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
            <Mono size={media.isMobile ? 19 : 21} weight={600}>{sel.last.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</Mono>
            <Mono size={14} weight={600} color={dirColor(dayChg)}>{(dayChg >= 0 ? '+' : '') + dayChg.toFixed(2) + '%'}</Mono>
            <span style={{ fontSize: 11, fontWeight: 600, color: T.muted, background: T.borderSoft, borderRadius: 99, padding: '3px 10px' }}>{sel.sub}</span>
          </div>
        </div>
        <div style={media.isMobile
          ? { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 6 }
          : { display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {emaPills.map(e => (
            <div key={e.label} style={{ textAlign: 'center', background: e.bg, borderRadius: 10, padding: '6px 12px' }}>
              <Mono size={12.5} weight={600} color={e.color}>{e.val}</Mono>
              <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.04em', color: T.faint, marginTop: 1 }}>{e.label}</div>
            </div>
          ))}
        </div>
      </Card>

      <Card style={{ marginTop: 14, padding: '12px 20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <Label style={{ letterSpacing: '0.08em' }}>Net new highs · 52-week</Label>
          <Mono size={13} weight={600} color={T.up}>{D.series.newHighs[D.series.newHighs.length - 1]}</Mono>
        </div>
        <svg viewBox="0 0 900 40" preserveAspectRatio="none" style={{ width: '100%', height: 38, marginTop: 6, display: 'block' }}>
          <path d={nnhStrip} fill={T.navy} opacity={0.75} />
        </svg>
      </Card>

      <Card style={{ marginTop: 14, padding: media.isMobile ? '14px 12px' : '18px 20px' }}>
        <div style={{ display: 'flex', gap: media.isMobile ? 6 : 12 }}>
          <svg viewBox="0 0 900 336" preserveAspectRatio="none" style={{ flex: 1, minWidth: 0, height: media.isMobile ? 240 : 340, display: 'block', overflow: 'visible' }}>
            <path d="M0 84 H900 M0 168 H900 M0 252 H900" stroke={T.borderSoft} strokeWidth={1} />
            <path d={chart.ema50} fill="none" stroke={T.navy} strokeWidth={1.5} strokeLinejoin="round" opacity={0.8} />
            <path d={chart.ema10} fill="none" stroke={T.amber} strokeWidth={1.5} strokeLinejoin="round" opacity={0.9} />
            <path d={chart.uw} fill={T.up} />
            <path d={chart.dw} fill={T.down} />
            <path d={chart.ub} fill={T.up} />
            <path d={chart.db} fill={T.down} />
            <path d={'M0 ' + chart.lastY + ' H900'} stroke={T.faint} strokeWidth={1} strokeDasharray="4 3" opacity={0.8} />
          </svg>
          <div style={{ width: media.isMobile ? 44 : 62, flexShrink: 0, height: media.isMobile ? 240 : 336, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '4px 0' }}>
            {chart.priceTicks.map((p, i) => (
              <div key={i} style={{ fontFamily: T.mono, fontSize: media.isMobile ? 9 : 10.5, color: T.faint, textAlign: 'right' }}>{p}</div>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', paddingRight: media.isMobile ? 44 : 62, marginTop: 8 }}>
          {/* Six date ticks crowd into each other on a phone — show every other. */}
          {(media.isMobile ? chartDates.filter((_, i) => i % 2 === 0) : chartDates)
            .map(d => <span key={d} style={{ fontFamily: T.mono, fontSize: media.isMobile ? 9.5 : 10.5, color: T.faint }}>{d}</span>)}
        </div>
        <div style={{ display: 'flex', gap: media.isMobile ? 10 : 18, flexWrap: 'wrap', marginTop: 12, paddingTop: 12, borderTop: '1px solid ' + T.borderSoft }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: T.muted }}><span style={{ width: 14, height: 2, background: T.amber, display: 'block' }} />10 EMA</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: T.muted }}><span style={{ width: 14, height: 2, background: T.navy, display: 'block' }} />50 EMA</span>
          <span style={{ fontSize: 12, color: T.faint }}>
            {baseCandles.length} sessions · daily candles · {realCandles ? 'NSE EOD (adjusted)' : 'sample data'}
          </span>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: media.isMobile ? '1fr' : '1fr 1fr', gap: 14, marginTop: 14 }}>
        {emaMinis.map(e => (
          <Card key={e.label} style={{ padding: '14px 18px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: T.text }}>% above {e.label}</span>
              <Mono size={16} weight={600} color={T.navy}>{e.value}%</Mono>
            </div>
            <svg viewBox="0 0 300 48" preserveAspectRatio="none" style={{ width: '100%', height: 48, marginTop: 6, display: 'block', overflow: 'visible' }}>
              <path d={e.area} fill={T.navySoft} stroke="none" />
              <path d={e.line} fill="none" stroke={T.navy} strokeWidth={1.75} strokeLinejoin="round" />
            </svg>
          </Card>
        ))}
      </div>
    </div>
  );
}
