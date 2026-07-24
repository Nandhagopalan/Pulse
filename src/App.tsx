import { useEffect, useMemo, useState } from 'react';
import pulseMark from './assets/pulse-mark.png';
import { buildData } from './lib/data';
import type { Stock } from './lib/data';
import { areaLine, arc, barsBottom, barsSigned, barsTop } from './lib/svg';
import { ohlc, candleChart } from './lib/candles';
import { fmtPct, fmtPrice } from './lib/format';

const UP = '#16A34A', DOWN = '#E11D48';

const DEFAULT_QUOTES = [
  'The trend is your friend until the end when it bends.',
  'Trade the setup, not the P&L.',
  'When breadth narrows, tighten stops — the market whispers before it shouts.',
  'Buy strength in strong sectors; never average down a swing trade.',
  'No setup, no trade. Cash is a position.',
];

type TabId = 'breadth' | 'chart' | 'sectors' | 'highs' | 'draw' | 'watch';
type HighMode = 'w52' | 'ath' | 'wk';
type CardMode = 'area' | 'bar';

function useWatchlist() {
  const [watch, setWatch] = useState<Record<string, true>>({});
  useEffect(() => {
    try {
      const w = localStorage.getItem('pulse-watchlist');
      if (w) setWatch(JSON.parse(w));
    } catch { /* ignore corrupt storage */ }
  }, []);
  const toggle = (sym: string) => {
    setWatch(prev => {
      const next = { ...prev };
      if (next[sym]) delete next[sym]; else next[sym] = true;
      try { localStorage.setItem('pulse-watchlist', JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  };
  return { watch, toggle };
}

function useQuotes() {
  const [quotes, setQuotes] = useState<string[]>(DEFAULT_QUOTES);
  const [quoteIdx, setQuoteIdx] = useState(0);
  useEffect(() => {
    try {
      const q = localStorage.getItem('pulse-quotes');
      const loaded = q ? JSON.parse(q) : DEFAULT_QUOTES;
      setQuotes(loaded);
      setQuoteIdx(Math.floor(Math.random() * loaded.length));
    } catch { /* ignore corrupt storage */ }
  }, []);
  const shuffle = () => {
    if (quotes.length < 2) return;
    let n = Math.floor(Math.random() * quotes.length);
    if (n === quoteIdx) n = (n + 1) % quotes.length;
    setQuoteIdx(n);
  };
  const addQuote = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const next = [...quotes, trimmed];
    setQuotes(next);
    setQuoteIdx(next.length - 1);
    try { localStorage.setItem('pulse-quotes', JSON.stringify(next)); } catch { /* ignore */ }
  };
  return { quoteText: quotes[quoteIdx % quotes.length], shuffle, addQuote };
}

function stockRow(s: Stock, watch: Record<string, true>, toggle: (sym: string) => void) {
  const starred = !!watch[s.sym];
  const tag = s.isATH ? 'ATH' : s.is52 ? '52W HIGH' : 'BREAKOUT';
  const tagBg = s.isATH ? '#101828' : s.is52 ? '#F0FDF4' : '#FEF6E7';
  const tagColor = s.isATH ? '#FFFFFF' : s.is52 ? '#15803D' : '#B45309';
  return {
    sym: s.sym, sector: s.sector, price: fmtPrice(s.price),
    chg1d: fmtPct(s.chg1d), c1Color: s.chg1d >= 0 ? UP : DOWN,
    chg1w: fmtPct(s.chg1w), cwColor: s.chg1w >= 0 ? UP : DOWN,
    fromAth: s.distATH < 0.05 ? '0.0%' : '-' + s.distATH.toFixed(1) + '%',
    tag, tagBg, tagColor,
    star: starred ? '★' : '☆', starColor: starred ? '#EAB308' : '#CBD2DC',
    toggle: () => toggle(s.sym),
  };
}

function buildCard(
  id: string, label: string, color: string, fill: string, valueColor: string,
  series: number[], defType: CardMode, barStyle: 'bottom' | 'top' | 'signed',
  mode: CardMode | undefined, setCard: (id: string, m: CardMode) => void,
) {
  const m = mode || defType;
  const last = series[series.length - 1];
  const value = (id === 'netHL' ? (last >= 0 ? '+' : '') : '') + Math.round(last);
  let area = '', line = '', bars = '', barsNeg = '', barsNegColor = color, baseline = '';
  if (m === 'area') {
    const p = areaLine(series, 300, 104, 8, 10); area = p.area; line = p.line;
  } else if (barStyle === 'signed') {
    const b = barsSigned(series, 300, 104, 8); bars = b.pos; barsNeg = b.neg; barsNegColor = '#E11D48'; baseline = 'M0 ' + b.zy + ' H300';
  } else if (barStyle === 'top') {
    bars = barsTop(series, 300, 104, 10);
  } else {
    bars = barsBottom(series, 300, 104, 10);
  }
  const A = '#FFFFFF', B = '#101828', G = '#667085';
  return {
    id, label, value, valueColor, color, fill, area, line, bars, barsNeg, barsNegColor, baseline,
    span: (id === 'newHighs' || id === 'newLows' || id === 'netHL') ? 4 : 6,
    setArea: () => setCard(id, 'area'), setBar: () => setCard(id, 'bar'),
    areaBtnBg: m === 'area' ? A : 'transparent', areaBtnColor: m === 'area' ? B : G,
    barBtnBg: m === 'bar' ? A : 'transparent', barBtnColor: m === 'bar' ? B : G,
  };
}

export default function App() {
  const [tab, setTab] = useState<TabId>('breadth');
  const [chartSym, setChartSym] = useState('NIFTY 50');
  const [highMode, setHighMode] = useState<HighMode>('w52');
  const [sectorFilter, setSectorFilter] = useState('All sectors');
  const [cardMode, setCardMode] = useState<Record<string, CardMode>>({});
  const [addingQuote, setAddingQuote] = useState(false);
  const [quoteDraft, setQuoteDraft] = useState('');

  const { watch, toggle } = useWatchlist();
  const { quoteText, shuffle, addQuote } = useQuotes();

  const D = useMemo(() => buildData(), []);

  const setCard = (id: string, m: CardMode) => setCardMode(prev => ({ ...prev, [id]: m }));

  const saveDraft = () => {
    addQuote(quoteDraft);
    setAddingQuote(false);
    setQuoteDraft('');
  };

  const tabs: { id: TabId; label: string }[] = [
    { id: 'breadth', label: 'Breadth' },
    { id: 'chart', label: 'Charts' },
    { id: 'sectors', label: 'Sectors' },
    { id: 'highs', label: 'Highs' },
    { id: 'draw', label: 'Drawdown' },
    { id: 'watch', label: 'Watchlist · ' + Object.keys(watch).length },
  ];

  // ---- breadth ----
  const total = D.advances + D.declines;
  const pctAdv = D.advances / total * 100;
  const f = Math.max(0, Math.min(1, pctAdv / 100));
  const gaugeColor = pctAdv >= 58 ? UP : pctAdv >= 46 ? '#F59E0B' : DOWN;
  const gaugeTrack = arc(110, 100, 82, 215, 505);
  const gaugeValue = arc(110, 100, 82, 215, 215 + 290 * f);

  const emaCharts = [
    ['20 EMA', D.emaVals.e20, D.emaHist.e20, '#2563EB', 'rgba(37,99,235,0.12)'],
    ['50 EMA', D.emaVals.e50, D.emaHist.e50, '#7C3AED', 'rgba(124,58,237,0.12)'],
    ['200 EMA', D.emaVals.e200, D.emaHist.e200, UP, 'rgba(22,163,74,0.12)'],
  ].map(([label, value, hist, color, fill]) => {
    const p = areaLine(hist as number[], 300, 54, 5, 6);
    return { label: label as string, value: (value as number).toFixed(1), color: color as string, fill: fill as string, line: p.line, area: p.area };
  });

  const adHist = barsSigned(D.adDaily, 300, 96, 6);
  const nhHist = barsSigned(D.nhDaily, 300, 96, 6);
  const netHighs = D.newHighs - D.newLows;

  const metricCards = [
    buildCard('newHighs', 'New Highs', UP, 'rgba(22,163,74,0.14)', UP, D.series.newHighs, 'bar', 'bottom', cardMode.newHighs, setCard),
    buildCard('newLows', 'New Lows', DOWN, 'rgba(225,29,72,0.14)', DOWN, D.series.newLows, 'bar', 'top', cardMode.newLows, setCard),
    buildCard('netHL', 'Net Highs − Lows', UP, 'rgba(22,163,74,0.14)', netHighs >= 0 ? UP : DOWN, D.series.netHL, 'bar', 'signed', cardMode.netHL, setCard),
    buildCard('up20', 'Up 20%+ in 5D', '#2563EB', 'rgba(37,99,235,0.14)', '#2563EB', D.series.up20, 'area', 'bottom', cardMode.up20, setCard),
    buildCard('up30', 'Up 30%+ in 5D', '#7C3AED', 'rgba(124,58,237,0.14)', '#7C3AED', D.series.up30, 'area', 'bottom', cardMode.up30, setCard),
    buildCard('up4vol', 'Up 4%+ on Volume', UP, 'rgba(22,163,74,0.14)', UP, D.series.up4vol, 'area', 'bottom', cardMode.up4vol, setCard),
    buildCard('down4vol', 'Down 4%+ on Volume', DOWN, 'rgba(225,29,72,0.14)', DOWN, D.series.down4vol, 'area', 'bottom', cardMode.down4vol, setCard),
  ];

  const riskScore = Math.round(0.4 * pctAdv + 0.28 * D.emaVals.e50 + 0.32 * D.emaVals.e200);
  const riskOn = riskScore >= 58, neutral = riskScore >= 46 && riskScore < 58;
  const regimeTag = riskOn ? 'RISK-ON' : neutral ? 'NEUTRAL' : 'RISK-OFF';
  const regimeColor = riskOn ? '#15803D' : neutral ? '#B45309' : '#BE123C';
  const regimeBg = riskOn ? '#F0FDF4' : neutral ? '#FEF6E7' : '#FEF2F4';
  const emaAbove = [D.emaVals.e20, D.emaVals.e50, D.emaVals.e200].filter(v => v >= 50).length;

  // ---- sectors ----
  const sectorRows = D.sectors.map(s => ({
    name: s.name, count: s.count, adv: s.adv, dec: s.dec,
    dmaPct: s.dmaPct, dmaWidth: s.dmaPct + '%', dmaColor: s.dmaPct >= 50 ? UP : '#F43F5E',
    newHighs: s.newHighs, wk: fmtPct(s.wk), wkColor: s.wk >= 0 ? UP : DOWN,
    score: s.score, scoreWidth: s.score + '%',
  }));

  // ---- highs ----
  const pools = {
    ath: D.stocks.filter(s => s.isATH),
    w52: D.stocks.filter(s => s.is52),
    wk: D.stocks.filter(s => s.wkBreak),
  };
  const highModes: { id: HighMode; label: string }[] = [
    { id: 'w52', label: '52-week highs' },
    { id: 'ath', label: 'All-time highs' },
    { id: 'wk', label: 'Weekly breakouts' },
  ];
  const sectorOptions = ['All sectors', ...D.sectors.map(s => s.name)];
  const highPool = pools[highMode].filter(s => sectorFilter === 'All sectors' || s.sector === sectorFilter).sort((a, b) => b.chg1w - a.chg1w);
  const highRows = highPool.map(s => stockRow(s, watch, toggle));

  // ---- drawdown ----
  const ddBucket = (d: number) => {
    if (d < 3) return { key: 'high', label: 'AT / NEAR HIGH', bg: '#F0FDF4', color: '#15803D' };
    if (d < 10) return { key: 'dip', label: 'MILD DIP', bg: '#F1F8E9', color: '#4D7C0F' };
    if (d < 20) return { key: 'corr', label: 'CORRECTION', bg: '#FEF6E7', color: '#B45309' };
    if (d < 40) return { key: 'bear', label: 'BEAR -20 to -40', bg: '#FEF0E7', color: '#C2410C' };
    return { key: 'deep', label: 'DEEP >40 · BASING', bg: '#FEF2F4', color: '#BE123C' };
  };
  const ddRows = [...D.stocks].sort((a, b) => b.distATH - a.distATH).map(s => {
    const b = ddBucket(s.distATH);
    return { ...stockRow(s, watch, toggle), ddLabel: b.label, ddBg: b.bg, ddColor: b.color, ddWidth: Math.min(100, Math.round(s.distATH / 60 * 100)) + '%', ddPct: '-' + s.distATH.toFixed(1) + '%' };
  });
  const buckets: [string, string, string][] = [
    ['high', 'At / near high', '#16A34A'], ['dip', 'Mild dip <10%', '#84CC16'],
    ['corr', 'Correction 10-20%', '#F59E0B'], ['bear', 'Bear 20-40%', '#F97316'], ['deep', 'Deep >40%', '#E11D48'],
  ];
  const ddCounts: Record<string, number> = { high: 0, dip: 0, corr: 0, bear: 0, deep: 0 };
  D.stocks.forEach(s => { ddCounts[ddBucket(s.distATH).key]++; });
  const ddTotal = D.stocks.length;
  const ddDist = buckets.map(([key, label, color]) => ({ label, color, count: ddCounts[key], pct: Math.round(ddCounts[key] / ddTotal * 100), width: (ddCounts[key] / ddTotal * 100) + '%' }));
  const bySector: Record<string, number[]> = {};
  D.stocks.forEach(s => { (bySector[s.sector] = bySector[s.sector] || []).push(s.distATH); });
  const ddSectors = Object.keys(bySector).map(name => {
    const arr = bySector[name]; const avg = arr.reduce((a, b) => a + b, 0) / arr.length;
    return { name, avg, count: arr.length };
  }).sort((a, b) => a.avg - b.avg);
  const ddMaxAvg = Math.max(...ddSectors.map(s => s.avg), 1);
  const ddSectorRows = ddSectors.map(s => ({ name: s.name, count: s.count, avg: '-' + s.avg.toFixed(1) + '%', avgWidth: Math.round(s.avg / ddMaxAvg * 100) + '%', avgColor: s.avg < 8 ? UP : s.avg < 20 ? '#F59E0B' : DOWN }));

  // ---- watchlist ----
  const watchRows = D.stocks.filter(s => watch[s.sym]).map(s => stockRow(s, watch, toggle));

  // ---- charts ----
  const idxSyms = D.indices.map(ix => ({ key: ix.name, label: ix.name, sub: 'Index', last: ix.value, vol: ix.name === 'INDIA VIX' ? 4 : 0.95 }));
  const watchSyms = D.stocks.filter(s => watch[s.sym]).map(s => ({ key: s.sym, label: s.sym, sub: s.sector, last: s.price, vol: 2.2 }));
  const allSyms = [...idxSyms, ...watchSyms];
  const sel = allSyms.find(x => x.key === chartSym) || allSyms[0];
  const candles = ohlc(sel.key, sel.last, sel.vol, 90);
  const chart = candleChart(candles, 900, 336, 8);
  const prevC = candles[candles.length - 2].c;
  const dayChg = (sel.last - prevC) / prevC * 100;
  const emaPills = [
    ['10 EMA', chart.ema10Last, chart.emaRaw.e10], ['20 EMA', chart.ema20Last, chart.emaRaw.e20],
    ['50 EMA', chart.ema50Last, chart.emaRaw.e50], ['200 EMA', chart.ema200Last, chart.emaRaw.e200],
  ].map(([label, val, raw]) => {
    const rawNum = raw as number;
    const above = chart.emaRaw.last >= rawNum;
    return { label: label as string, val: val as string, above, color: above ? '#15803D' : '#BE123C', bg: above ? '#F0FDF4' : '#FEF2F4' };
  });
  const nnhStrip = barsBottom(D.series.newHighs, 900, 40, 4);
  const chartOptions = allSyms.map(x => x.key);
  const chartDates = ['24 Apr', '12 May', '30 May', '18 Jun', '07 Jul', '23 Jul'];

  const indices = D.indices.map(ix => {
    const p = areaLine(ix.pts, 100, 30, 3, 3);
    const isVix = ix.name === 'INDIA VIX'; const pos = ix.chgPct >= 0;
    const color = isVix ? '#B45309' : (pos ? UP : DOWN);
    const fill = isVix ? 'rgba(245,158,11,0.12)' : (pos ? 'rgba(22,163,74,0.12)' : 'rgba(225,29,72,0.13)');
    return {
      name: ix.name,
      value: ix.value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      chg: (ix.chgPct >= 0 ? '+' : '') + ix.chgPct.toFixed(2) + '%',
      color, fill, line: p.line, area: p.area,
      open: () => { setTab('chart'); setChartSym(ix.name); },
    };
  });

  const advWidth = Math.round(D.advances / (D.advances + D.declines + D.unchanged) * 100) + '%';
  const decWidth = Math.round(D.declines / (D.advances + D.declines + D.unchanged) * 100) + '%';
  const advPct = (D.advances / (D.advances + D.declines + D.unchanged) * 100).toFixed(0);
  const decPct = (D.declines / (D.advances + D.declines + D.unchanged) * 100).toFixed(0);
  const regimeStats = emaAbove + ' of 3 EMA gauges above 50 · net new highs ' + (netHighs >= 0 ? '+' : '') + netHighs + ' expanding · ' + pctAdv.toFixed(0) + '% advancing';
  const regimeVerdict = riskOn ? 'favourable — press winners, add on breakouts' : neutral ? 'mixed — be selective, normal size' : 'unfavourable — minimum size or stand aside';
  const highsFootnote = highRows.length + ' stocks · sorted by 1-week momentum · star a row to add it to your watchlist';
  const ddFootnote = ddRows.length + ' stocks · sorted by depth of fall from all-time high';

  return (
    <div style={{ minHeight: '100vh', background: '#F1F2F4' }}>
      {/* Top bar */}
      <div style={{ background: '#FFFFFF', borderBottom: '1px solid #E8EAED' }}>
        <div style={{ maxWidth: 1280, margin: '0 auto', padding: '12px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
            <img src={pulseMark} alt="Pulse" style={{ height: 30, width: 'auto', display: 'block' }} />
            <div style={{ lineHeight: 1 }}>
              <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: '0.02em', color: '#101828' }}>PULSE</div>
              <div style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '0.18em', color: '#98A2B3', marginTop: 3 }}>SWING TRADER TERMINAL</div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: '#667085' }}>Thu 23 Jul 2026 · 13:16 IST</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 99, padding: '4px 11px' }}>
              <span style={{ width: 7, height: 7, borderRadius: 99, background: UP, display: 'block' }} />
              <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', color: '#15803D' }}>MARKET OPEN</span>
            </div>
          </div>
        </div>
      </div>

      <div style={{ maxWidth: 1280, margin: '0 auto', padding: '20px 28px 72px' }}>
        {/* Index strip */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
          {indices.map(ix => (
            <div key={ix.name} onClick={ix.open} title="Open chart" style={{ minWidth: 0, cursor: 'pointer', background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 14, padding: '11px 13px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
                <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.02em', color: '#667085', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{ix.name}</span>
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: ix.color, whiteSpace: 'nowrap' }}>{ix.chg}</span>
              </div>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 15, fontWeight: 600, marginTop: 4, color: '#101828' }}>{ix.value}</div>
              <svg viewBox="0 0 100 30" preserveAspectRatio="none" style={{ width: '100%', height: 30, marginTop: 7, display: 'block', overflow: 'visible' }}>
                <path d={ix.area} fill={ix.fill} stroke="none" />
                <path d={ix.line} fill="none" stroke={ix.color} strokeWidth={1.5} strokeLinejoin="round" />
              </svg>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginTop: 22, borderBottom: '1px solid #E8EAED' }}>
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} style={{ appearance: 'none', border: 'none', cursor: 'pointer', fontFamily: "'Albert Sans', sans-serif", fontSize: 14, fontWeight: 600, padding: '11px 16px 13px', background: 'transparent', color: tab === t.id ? '#101828' : '#98A2B3', borderBottom: '2px solid ' + (tab === t.id ? '#16A34A' : 'transparent'), marginBottom: -1 }}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Quote */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16, padding: '10px 16px', background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 12, boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: UP }}>RULE</span>
          <div style={{ fontSize: 13.5, color: '#475467', fontStyle: 'italic', flex: 1, minWidth: 0 }}>&ldquo;{quoteText}&rdquo;</div>
          <button onClick={shuffle} title="Next" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 14, color: '#98A2B3', padding: '2px 4px', lineHeight: 1 }}>&#8635;</button>
          <button onClick={() => setAddingQuote(v => !v)} style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: "'Albert Sans', sans-serif", fontSize: 12.5, fontWeight: 600, color: '#98A2B3', padding: '2px 4px' }}>{addingQuote ? 'Cancel' : '+ Add rule'}</button>
        </div>
        {addingQuote && (
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <input
              value={quoteDraft}
              onChange={e => setQuoteDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveDraft(); }}
              placeholder="Write a rule or reminder for yourself…"
              style={{ flex: 1, fontFamily: "'Albert Sans', sans-serif", fontSize: 13.5, padding: '9px 14px', border: '1px solid #E8EAED', borderRadius: 8, background: '#FFFFFF', color: '#101828', outline: 'none' }}
            />
            <button onClick={saveDraft} style={{ appearance: 'none', cursor: 'pointer', fontFamily: "'Albert Sans', sans-serif", fontSize: 13, fontWeight: 600, padding: '9px 16px', borderRadius: 8, border: 'none', background: '#101828', color: '#FFFFFF' }}>Save</button>
          </div>
        )}

        {/* BREADTH */}
        {tab === 'breadth' && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1.45fr', gap: 16, marginTop: 18 }}>
              <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '22px 24px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#98A2B3' }}>Market Pulse</div>
                <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: '-0.02em', marginTop: 2 }}>Breadth Thrust</div>
                <div style={{ position: 'relative', width: 220, margin: '14px auto 6px' }}>
                  <svg viewBox="0 0 220 200" style={{ width: 220, height: 200, display: 'block' }}>
                    <path d={gaugeTrack} fill="none" stroke="#EDEFF2" strokeWidth={18} strokeLinecap="round" />
                    <path d={gaugeValue} fill="none" stroke={gaugeColor} strokeWidth={18} strokeLinecap="round" />
                  </svg>
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', paddingBottom: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline' }}>
                      <span style={{ fontSize: 52, fontWeight: 800, letterSpacing: '-0.03em', color: '#101828', lineHeight: 1 }}>{pctAdv.toFixed(1)}</span>
                      <span style={{ fontSize: 20, fontWeight: 700, color: '#98A2B3' }}>%</span>
                    </div>
                    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#98A2B3', marginTop: 8 }}>OF UNIVERSE ADVANCING</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 10, marginTop: 4 }}>
                  <span style={{ fontSize: 30, fontWeight: 800, color: UP }}>{D.advances.toLocaleString('en-IN')}</span>
                  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#98A2B3' }}>ADVANCES</span>
                  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#C0C6D0' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#98A2B3' }}>DECLINES</span>
                  <span style={{ fontSize: 30, fontWeight: 800, color: DOWN }}>{D.declines.toLocaleString('en-IN')}</span>
                </div>
                <div style={{ display: 'flex', gap: 3, height: 9, marginTop: 12 }}>
                  <div style={{ width: advWidth, background: UP, borderRadius: 99 }} />
                  <div style={{ width: decWidth, background: DOWN, borderRadius: 99 }} />
                </div>
                <div style={{ textAlign: 'center', fontSize: 11.5, color: '#98A2B3', marginTop: 8 }}>Universe {D.universe.toLocaleString('en-IN')} NSE stocks · {advPct}% adv / {decPct}% dec</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#475467', background: '#F2F4F7', borderRadius: 99, padding: '6px 12px' }}>+20% in 5D · <span style={{ fontWeight: 700, color: UP }}>{D.series.up20[44]}</span></div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#475467', background: '#F2F4F7', borderRadius: 99, padding: '6px 12px' }}>4% vol moves · <span style={{ color: UP, fontWeight: 700 }}>{D.volUp}&#9650;</span> / <span style={{ color: DOWN, fontWeight: 700 }}>{D.volDn}&#9660;</span></div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#475467', background: '#F2F4F7', borderRadius: 99, padding: '6px 12px' }}>A/D 2 up days</div>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateRows: 'repeat(3, 1fr)', gap: 12 }}>
                {emaCharts.map(e => (
                  <div key={e.label} style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '14px 18px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: '#475467' }}>% above {e.label}</span>
                      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 19, fontWeight: 600, color: e.color }}>{e.value}%</span>
                    </div>
                    <svg viewBox="0 0 300 54" preserveAspectRatio="none" style={{ width: '100%', height: 54, marginTop: 6, display: 'block', overflow: 'visible' }}>
                      <path d="M0 27 H300" stroke="#EEF0F2" strokeWidth={1} strokeDasharray="3 3" />
                      <path d={e.area} fill={e.fill} stroke="none" />
                      <path d={e.line} fill="none" stroke={e.color} strokeWidth={1.75} strokeLinejoin="round" />
                    </svg>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 16 }}>
              <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '18px 20px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#344054' }}>Daily Advances &minus; Declines</div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#667085', background: '#F2F4F7', borderRadius: 99, padding: '3px 10px' }}>latest {(D.adDaily[89] >= 0 ? '+' : '') + D.adDaily[89]}</div>
                </div>
                <svg viewBox="0 0 300 96" preserveAspectRatio="none" style={{ width: '100%', height: 96, marginTop: 14, display: 'block', overflow: 'visible' }}>
                  <path d={'M0 ' + adHist.zy + ' H300'} stroke="#E7EAEE" strokeWidth={1} />
                  <path d={adHist.pos} fill={UP} />
                  <path d={adHist.neg} fill={DOWN} />
                </svg>
              </div>
              <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '18px 20px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#344054' }}>Net Highs &minus; Lows</div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#667085', background: '#F2F4F7', borderRadius: 99, padding: '3px 10px' }}>NNH {(D.nhDaily[89] >= 0 ? '+' : '') + D.nhDaily[89]}</div>
                </div>
                <svg viewBox="0 0 300 96" preserveAspectRatio="none" style={{ width: '100%', height: 96, marginTop: 14, display: 'block', overflow: 'visible' }}>
                  <path d={'M0 ' + nhHist.zy + ' H300'} stroke="#E7EAEE" strokeWidth={1} />
                  <path d={nhHist.pos} fill={UP} />
                  <path d={nhHist.neg} fill={DOWN} />
                </svg>
              </div>
            </div>

            <div style={{ marginTop: 16, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 16, boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.06em', color: regimeColor, background: regimeBg, borderRadius: 99, padding: '5px 12px' }}>{regimeTag}</span>
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 15, fontWeight: 600, color: '#101828' }}>{riskScore}<span style={{ color: '#98A2B3', fontSize: 12 }}>/100</span></span>
              </div>
              <div style={{ fontSize: 13.5, color: '#475467', flex: 1 }}>{regimeStats} &mdash; <span style={{ fontWeight: 700, color: regimeColor }}>{regimeVerdict}</span></div>
            </div>

            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 26 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#101828' }}>Momentum &amp; Participation</div>
              <div style={{ fontSize: 12, color: '#98A2B3' }}>daily counts across the universe</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 14, marginTop: 14 }}>
              {metricCards.map(c => (
                <div key={c.id} style={{ gridColumn: 'span ' + c.span, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '16px 18px 12px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#344054' }}>{c.label}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{ display: 'flex', gap: 2, background: '#F2F4F7', borderRadius: 8, padding: 2 }}>
                        <button onClick={c.setArea} title="Line" style={{ appearance: 'none', border: 'none', cursor: 'pointer', borderRadius: 6, padding: '3px 6px', background: c.areaBtnBg, color: c.areaBtnColor, fontSize: 12, lineHeight: 1 }}>&#8961;</button>
                        <button onClick={c.setBar} title="Bar" style={{ appearance: 'none', border: 'none', cursor: 'pointer', borderRadius: 6, padding: '3px 6px', background: c.barBtnBg, color: c.barBtnColor, fontSize: 12, lineHeight: 1 }}>&#9776;</button>
                      </div>
                      <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-0.02em', color: c.valueColor }}>{c.value}</div>
                    </div>
                  </div>
                  <svg viewBox="0 0 300 104" preserveAspectRatio="none" style={{ width: '100%', height: 104, marginTop: 10, display: 'block', overflow: 'visible' }}>
                    <path d="M0 34 H300 M0 68 H300" stroke="#F1F3F5" strokeWidth={1} />
                    <path d={c.baseline} stroke="#E7EAEE" strokeWidth={1} />
                    <path d={c.area} fill={c.fill} stroke="none" />
                    <path d={c.line} fill="none" stroke={c.color} strokeWidth={1.75} strokeLinejoin="round" />
                    <path d={c.bars} fill={c.color} />
                    <path d={c.barsNeg} fill={c.barsNegColor} />
                  </svg>
                </div>
              ))}
            </div>
          </>
        )}

        {/* CHARTS */}
        {tab === 'chart' && (
          <div style={{ marginTop: 18 }}>
            <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '16px 20px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <select value={chartSym} onChange={e => setChartSym(e.target.value)} style={{ fontFamily: "'Albert Sans', sans-serif", fontSize: 16, fontWeight: 700, padding: '8px 12px', borderRadius: 10, border: '1px solid #E8EAED', background: '#FAFBFC', color: '#101828' }}>
                  {chartOptions.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 22, fontWeight: 600, color: '#101828' }}>{sel.last.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 14, fontWeight: 600, color: dayChg >= 0 ? UP : DOWN }}>{(dayChg >= 0 ? '+' : '') + dayChg.toFixed(2) + '%'}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#98A2B3', background: '#F2F4F7', borderRadius: 99, padding: '3px 10px' }}>{sel.sub}</span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {emaPills.map(e => (
                  <div key={e.label} style={{ textAlign: 'center', background: e.bg, borderRadius: 10, padding: '6px 12px' }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 600, color: e.color }}>{e.val}</div>
                    <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.04em', color: '#98A2B3', marginTop: 1 }}>{e.label}</div>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ marginTop: 14, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '12px 20px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#98A2B3' }}>Net new highs · 52-week</span>
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 600, color: UP }}>{D.series.newHighs[D.series.newHighs.length - 1]}</span>
              </div>
              <svg viewBox="0 0 900 40" preserveAspectRatio="none" style={{ width: '100%', height: 38, marginTop: 6, display: 'block' }}>
                <path d={nnhStrip} fill={UP} />
              </svg>
            </div>

            <div style={{ marginTop: 14, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '18px 20px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ display: 'flex', gap: 12 }}>
                <svg viewBox="0 0 900 336" preserveAspectRatio="none" style={{ flex: 1, minWidth: 0, height: 340, display: 'block', overflow: 'visible' }}>
                  <path d="M0 84 H900 M0 168 H900 M0 252 H900" stroke="#F1F3F5" strokeWidth={1} />
                  <path d={chart.ema50} fill="none" stroke="#7C3AED" strokeWidth={1.5} strokeLinejoin="round" opacity={0.85} />
                  <path d={chart.ema10} fill="none" stroke="#0EA5E9" strokeWidth={1.5} strokeLinejoin="round" opacity={0.85} />
                  <path d={chart.uw} fill={UP} />
                  <path d={chart.dw} fill={DOWN} />
                  <path d={chart.ub} fill={UP} />
                  <path d={chart.db} fill={DOWN} />
                  <path d={'M0 ' + chart.lastY + ' H900'} stroke={chart.lastColor} strokeWidth={1} strokeDasharray="4 3" opacity={0.7} />
                </svg>
                <div style={{ width: 62, height: 336, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '4px 0' }}>
                  {chart.priceTicks.map((p, i) => (
                    <div key={i} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, color: '#98A2B3', textAlign: 'right' }}>{p}</div>
                  ))}
                </div>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', paddingRight: 62, marginTop: 8 }}>
                {chartDates.map(d => <span key={d} style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, color: '#98A2B3' }}>{d}</span>)}
              </div>
              <div style={{ display: 'flex', gap: 18, marginTop: 12, paddingTop: 12, borderTop: '1px solid #F2F4F6' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#667085' }}><span style={{ width: 14, height: 2, background: '#0EA5E9', display: 'block' }} />10 EMA</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#667085' }}><span style={{ width: 14, height: 2, background: '#7C3AED', display: 'block' }} />50 EMA</span>
                <span style={{ fontSize: 12, color: '#98A2B3' }}>90 sessions · daily candles · sample data</span>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 14 }}>
              {emaCharts.slice(0, 2).map(e => (
                <div key={e.label} style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '14px 18px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: '#475467' }}>% above {e.label}</span>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 17, fontWeight: 600, color: e.color }}>{e.value}%</span>
                  </div>
                  <svg viewBox="0 0 300 48" preserveAspectRatio="none" style={{ width: '100%', height: 48, marginTop: 6, display: 'block', overflow: 'visible' }}>
                    <path d={e.area} fill={e.fill} stroke="none" />
                    <path d={e.line} fill="none" stroke={e.color} strokeWidth={1.75} strokeLinejoin="round" />
                  </svg>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* SECTORS */}
        {tab === 'sectors' && (
          <div style={{ marginTop: 18, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, overflow: 'hidden', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 0.7fr 0.9fr 1.4fr 0.8fr 0.7fr 1.2fr', gap: 14, padding: '13px 22px', background: '#FAFBFC', borderBottom: '1px solid #E8EAED', fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
              <div>Sector</div><div style={{ textAlign: 'right' }}>Stocks</div><div style={{ textAlign: 'right' }}>Adv / Dec</div><div>% above 50 DMA</div><div style={{ textAlign: 'right' }}>New highs</div><div style={{ textAlign: 'right' }}>1W %</div><div>Strength</div>
            </div>
            {sectorRows.map(s => (
              <div key={s.name} style={{ display: 'grid', gridTemplateColumns: '1.5fr 0.7fr 0.9fr 1.4fr 0.8fr 0.7fr 1.2fr', gap: 14, alignItems: 'center', padding: '12px 22px', borderBottom: '1px solid #F2F4F6', fontSize: 13.5 }}>
                <div style={{ fontWeight: 600 }}>{s.name}</div>
                <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", color: '#667085', fontSize: 13 }}>{s.count}</div>
                <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}><span style={{ color: UP }}>{s.adv}</span><span style={{ color: '#C0C6D0' }}> / </span><span style={{ color: DOWN }}>{s.dec}</span></div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{ flex: 1, height: 6, background: '#F2F4F6', borderRadius: 99, overflow: 'hidden' }}><div style={{ height: '100%', width: s.dmaWidth, background: s.dmaColor, borderRadius: 99 }} /></div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, fontWeight: 600, width: 36, textAlign: 'right' }}>{s.dmaPct}%</div>
                </div>
                <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 600 }}>{s.newHighs}</div>
                <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 500, color: s.wkColor }}>{s.wk}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{ flex: 1, height: 6, background: '#F2F4F6', borderRadius: 99, overflow: 'hidden' }}><div style={{ height: '100%', width: s.scoreWidth, background: '#101828', borderRadius: 99 }} /></div>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, fontWeight: 600, width: 24, textAlign: 'right' }}>{s.score}</div>
                </div>
              </div>
            ))}
            <div style={{ padding: '13px 22px', fontSize: 12, color: '#98A2B3' }}>Strength = composite of % above 50 DMA, weekly momentum and rate of new highs. Ranked strongest first.</div>
          </div>
        )}

        {/* HIGHS */}
        {tab === 'highs' && (
          <div style={{ marginTop: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', gap: 6 }}>
                {highModes.map(m => {
                  const active = highMode === m.id;
                  return (
                    <button key={m.id} onClick={() => setHighMode(m.id)} style={{ appearance: 'none', cursor: 'pointer', fontFamily: "'Albert Sans', sans-serif", fontSize: 13, fontWeight: 600, padding: '7px 14px', borderRadius: 99, border: '1px solid ' + (active ? '#101828' : '#E8EAED'), background: active ? '#101828' : '#FFFFFF', color: active ? '#FFFFFF' : '#344054' }}>
                      {m.label} · {pools[m.id].length}
                    </button>
                  );
                })}
              </div>
              <select value={sectorFilter} onChange={e => setSectorFilter(e.target.value)} style={{ fontFamily: "'Albert Sans', sans-serif", fontSize: 13, fontWeight: 500, padding: '8px 12px', borderRadius: 8, border: '1px solid #E8EAED', background: '#FFFFFF', color: '#344054' }}>
                {sectorOptions.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>

            <div style={{ marginTop: 16, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, overflow: 'hidden', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '34px 1.1fr 1.1fr 0.9fr 0.6fr 0.6fr 0.9fr 0.7fr', gap: 14, padding: '13px 22px', background: '#FAFBFC', borderBottom: '1px solid #E8EAED', fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                <div></div><div>Symbol</div><div>Sector</div><div style={{ textAlign: 'right' }}>Price</div><div style={{ textAlign: 'right' }}>1D %</div><div style={{ textAlign: 'right' }}>1W %</div><div style={{ textAlign: 'right' }}>From ATH</div><div style={{ textAlign: 'right' }}>Tag</div>
              </div>
              {highRows.map(r => (
                <div key={r.sym} style={{ display: 'grid', gridTemplateColumns: '34px 1.1fr 1.1fr 0.9fr 0.6fr 0.6fr 0.9fr 0.7fr', gap: 14, alignItems: 'center', padding: '11px 22px', borderBottom: '1px solid #F2F4F6', fontSize: 13.5 }}>
                  <button onClick={r.toggle} title="Watchlist" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, color: r.starColor, padding: 0, lineHeight: 1 }}>{r.star}</button>
                  <div style={{ fontWeight: 700, fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>{r.sym}</div>
                  <div style={{ color: '#667085', fontSize: 13 }}>{r.sector}</div>
                  <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>{r.price}</div>
                  <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 500, color: r.c1Color }}>{r.chg1d}</div>
                  <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 500, color: r.cwColor }}>{r.chg1w}</div>
                  <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, color: '#667085' }}>{r.fromAth}</div>
                  <div style={{ textAlign: 'right' }}><span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.03em', padding: '3px 8px', borderRadius: 6, background: r.tagBg, color: r.tagColor }}>{r.tag}</span></div>
                </div>
              ))}
              <div style={{ padding: '13px 22px', fontSize: 12, color: '#98A2B3' }}>{highsFootnote}</div>
            </div>
          </div>
        )}

        {/* DRAWDOWN */}
        {tab === 'draw' && (
          <div style={{ marginTop: 18 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 16 }}>
              <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '20px 22px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.06em', textTransform: 'uppercase' }}>How far off all-time highs</div>
                <div style={{ display: 'flex', height: 12, borderRadius: 99, overflow: 'hidden', marginTop: 16, background: '#F2F4F6' }}>
                  {ddDist.map(d => <div key={d.label} title={d.label} style={{ width: d.width, background: d.color }} />)}
                </div>
                <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
                  {ddDist.map(d => (
                    <div key={d.label} style={{ display: 'grid', gridTemplateColumns: '12px 1fr auto auto', alignItems: 'center', gap: 10 }}>
                      <div style={{ width: 10, height: 10, borderRadius: 3, background: d.color }} />
                      <div style={{ fontSize: 13, color: '#344054' }}>{d.label}</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 600 }}>{d.count}</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: '#98A2B3', width: 34, textAlign: 'right' }}>{d.pct}%</div>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '20px 22px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Sector avg drawdown · most resilient first</div>
                <div style={{ display: 'grid', gap: 9, marginTop: 16 }}>
                  {ddSectorRows.map(s => (
                    <div key={s.name} style={{ display: 'grid', gridTemplateColumns: '96px 1fr 52px', alignItems: 'center', gap: 12 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 600 }}>{s.name}</div>
                      <div style={{ height: 7, background: '#F2F4F6', borderRadius: 99, overflow: 'hidden' }}><div style={{ height: '100%', width: s.avgWidth, background: s.avgColor, borderRadius: 99 }} /></div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, fontWeight: 600, textAlign: 'right', color: s.avgColor }}>{s.avg}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div style={{ marginTop: 16, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, overflow: 'hidden', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '34px 1.1fr 1fr 0.9fr 1.4fr 1.2fr', gap: 14, padding: '13px 22px', background: '#FAFBFC', borderBottom: '1px solid #E8EAED', fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                <div></div><div>Symbol</div><div>Sector</div><div style={{ textAlign: 'right' }}>Price</div><div>Fall from ATH</div><div style={{ textAlign: 'right' }}>Status</div>
              </div>
              {ddRows.map(r => (
                <div key={r.sym} style={{ display: 'grid', gridTemplateColumns: '34px 1.1fr 1fr 0.9fr 1.4fr 1.2fr', gap: 14, alignItems: 'center', padding: '11px 22px', borderBottom: '1px solid #F2F4F6', fontSize: 13.5 }}>
                  <button onClick={r.toggle} title="Watchlist" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, color: r.starColor, padding: 0, lineHeight: 1 }}>{r.star}</button>
                  <div style={{ fontWeight: 700, fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>{r.sym}</div>
                  <div style={{ color: '#667085', fontSize: 13 }}>{r.sector}</div>
                  <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>{r.price}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ flex: 1, height: 6, background: '#F2F4F6', borderRadius: 99, overflow: 'hidden' }}><div style={{ height: '100%', width: r.ddWidth, background: r.ddColor, borderRadius: 99 }} /></div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, fontWeight: 600, width: 48, textAlign: 'right', color: r.ddColor }}>{r.ddPct}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}><span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.03em', padding: '3px 8px', borderRadius: 6, background: r.ddBg, color: r.ddColor }}>{r.ddLabel}</span></div>
                </div>
              ))}
              <div style={{ padding: '13px 22px', fontSize: 12, color: '#98A2B3' }}>{ddFootnote}</div>
            </div>
          </div>
        )}

        {/* WATCHLIST */}
        {tab === 'watch' && (
          <div style={{ marginTop: 18 }}>
            {watchRows.length === 0 && (
              <div style={{ background: '#FFFFFF', border: '1px dashed #D0D5DD', borderRadius: 16, padding: '48px 22px', textAlign: 'center' }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#344054' }}>Nothing tracked yet</div>
                <div style={{ fontSize: 13.5, color: '#667085', marginTop: 6 }}>Star stocks from the Highs or Drawdown tab to build your swing watchlist.</div>
              </div>
            )}
            {watchRows.length > 0 && (
              <div style={{ background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, overflow: 'hidden', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '34px 1.1fr 1.1fr 0.9fr 0.6fr 0.6fr 0.9fr 0.7fr', gap: 14, padding: '13px 22px', background: '#FAFBFC', borderBottom: '1px solid #E8EAED', fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                  <div></div><div>Symbol</div><div>Sector</div><div style={{ textAlign: 'right' }}>Price</div><div style={{ textAlign: 'right' }}>1D %</div><div style={{ textAlign: 'right' }}>1W %</div><div style={{ textAlign: 'right' }}>From ATH</div><div style={{ textAlign: 'right' }}>Tag</div>
                </div>
                {watchRows.map(r => (
                  <div key={r.sym} style={{ display: 'grid', gridTemplateColumns: '34px 1.1fr 1.1fr 0.9fr 0.6fr 0.6fr 0.9fr 0.7fr', gap: 14, alignItems: 'center', padding: '11px 22px', borderBottom: '1px solid #F2F4F6', fontSize: 13.5 }}>
                    <button onClick={r.toggle} title="Remove" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, color: r.starColor, padding: 0, lineHeight: 1 }}>{r.star}</button>
                    <div style={{ fontWeight: 700, fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>{r.sym}</div>
                    <div style={{ color: '#667085', fontSize: 13 }}>{r.sector}</div>
                    <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 }}>{r.price}</div>
                    <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 500, color: r.c1Color }}>{r.chg1d}</div>
                    <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, fontWeight: 500, color: r.cwColor }}>{r.chg1w}</div>
                    <div style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, color: '#667085' }}>{r.fromAth}</div>
                    <div style={{ textAlign: 'right' }}><span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.03em', padding: '3px 8px', borderRadius: 6, background: r.tagBg, color: r.tagColor }}>{r.tag}</span></div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: 16, background: '#FFFFFF', border: '1px solid #E8EAED', borderRadius: 16, padding: '18px 22px', boxShadow: '0 1px 2px rgba(16,24,40,0.04)' }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#98A2B3', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Coming next</div>
              <div style={{ fontSize: 13.5, color: '#667085', marginTop: 8, lineHeight: 1.6 }}>Per-stock fundamentals (earnings, ROE, promoter holding) and a curated news feed will live here — click a watchlist row to open its detail panel.</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
