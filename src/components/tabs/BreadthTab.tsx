import type { ReactNode } from 'react';
import { T, dirColor } from '../../theme';
import { Card, Label, Mono, Serif } from '../ui';
import type { MarketData } from '../../lib/data';
import { areaLine, arc, barsBottom, barsTop, barsSigned } from '../../lib/svg';
import { fmtDay, fmtDayYear, tail, windowNote, rangeLabel } from '../../lib/window';

type CardMode = 'area' | 'bar';

export type BreadthRange = '1w' | '1m' | '3m' | '6m';

// Sessions per range. NSE trades ~21 sessions a month, so these are the real
// bar counts behind each label rather than calendar days.
export const RANGES: { id: BreadthRange; label: string; sessions: number }[] = [
  { id: '1w', label: '1W', sessions: 5 },
  { id: '1m', label: '1M', sessions: 21 },
  { id: '3m', label: '3M', sessions: 63 },
  { id: '6m', label: '6M', sessions: 120 },
];

/** Clamp a requested window to what the series actually holds. */
function windowOf(series: number[], sessions: number) {
  const n = Math.max(2, Math.min(sessions, series.length));
  return { vals: series.slice(series.length - n), n };
}

/** Date ticks under a chart: first, middle and last session of the window. */
function DateAxis({ dates, n }: { dates: string[]; n: number }) {
  const t = tail(dates, n);
  if (t.length < 2) return null;
  const mid = t[Math.floor((t.length - 1) / 2)];
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 10, color: T.faint, fontFamily: T.mono }}>
      <span>{fmtDay(t[0])}</span>
      <span>{fmtDay(mid)}</span>
      <span>{fmtDay(t[t.length - 1])}</span>
    </div>
  );
}

/** Small "what am I looking at" line under a chart title. */
function WindowNote({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 10.5, color: T.faint, marginTop: 3, lineHeight: 1.35 }}>{children}</div>;
}

function SignedChart({ title, badge, vals, dates, n, note }: {
  title: string; badge: string; vals: number[]; dates: string[]; n: number; note: string;
}) {
  const h = barsSigned(vals, 300, 96, 6);
  const pos = vals.filter(v => v > 0).length;
  return (
    <Card style={{ padding: '18px 20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>{title}</div>
        <div style={{ fontSize: 11, fontWeight: 600, color: T.muted, background: T.borderSoft, borderRadius: 99, padding: '3px 10px', whiteSpace: 'nowrap' }}>{badge}</div>
      </div>
      <WindowNote>{note}</WindowNote>
      <svg viewBox="0 0 300 96" preserveAspectRatio="none" style={{ width: '100%', height: 96, marginTop: 12, display: 'block', overflow: 'visible' }}>
        <path d={'M0 ' + h.zy + ' H300'} stroke={T.border} strokeWidth={1} />
        <path d={h.pos} fill={T.up} />
        <path d={h.neg} fill={T.down} />
      </svg>
      <DateAxis dates={dates} n={n} />
      <div style={{ fontSize: 11, color: T.muted, marginTop: 6 }}>
        <Mono size={11} weight={600} color={T.up}>{pos}</Mono> up days vs{' '}
        <Mono size={11} weight={600} color={T.down}>{vals.length - pos}</Mono> down days in this window
      </div>
    </Card>
  );
}

function FlowsChart({ fii, dii, dates }: { fii: number[]; dii: number[]; dates: string[] }) {
  const w = 300, h = 96, pad = 6;
  const m = Math.max(...fii.map(Math.abs), ...dii.map(Math.abs), 1);
  const zy = h / 2;
  const step = w / Math.max(1, fii.length);
  const bw = step * 0.3;
  const bar = (v: number, x: number) => {
    const bh = Math.abs(v) / m * (h / 2 - pad);
    const y = v >= 0 ? zy - bh : zy;
    return 'M' + x.toFixed(1) + ' ' + y.toFixed(1) + ' h' + bw.toFixed(1) + ' v' + bh.toFixed(1) + ' h' + (-bw).toFixed(1) + ' Z ';
  };
  let fiiPath = '', diiPath = '';
  fii.forEach((v, i) => { fiiPath += bar(v, i * step + step * 0.14); });
  dii.forEach((v, i) => { diiPath += bar(v, i * step + step * 0.52); });
  const lastF = fii[fii.length - 1] ?? 0, lastD = dii[dii.length - 1] ?? 0;
  const sumF = Math.round(fii.reduce((a, b) => a + b, 0));
  const sumD = Math.round(dii.reduce((a, b) => a + b, 0));
  const range = rangeLabel(dates, fii.length);
  return (
    <Card style={{ padding: '18px 20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>FII / DII Flows · ₹ Cr</div>
        <div style={{ display: 'flex', gap: 6 }}>
          <Mono size={11.5} color={dirColor(lastF)}>{(lastF >= 0 ? '+' : '') + lastF.toLocaleString('en-IN')} FII</Mono>
          <Mono size={11.5} color={dirColor(lastD)}>{(lastD >= 0 ? '+' : '') + lastD.toLocaleString('en-IN')} DII</Mono>
        </div>
      </div>
      <WindowNote>Net cash-market buying per session · last {fii.length} sessions{range ? ' · ' + range : ''}</WindowNote>
      <svg viewBox="0 0 300 96" preserveAspectRatio="none" style={{ width: '100%', height: 96, marginTop: 12, display: 'block', overflow: 'visible' }}>
        <path d={'M0 ' + zy + ' H300'} stroke={T.border} strokeWidth={1} />
        <path d={fiiPath} fill={T.amber} />
        <path d={diiPath} fill={T.navy} />
      </svg>
      <DateAxis dates={dates} n={fii.length} />
      <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 11, color: T.muted, flexWrap: 'wrap' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: T.amber }} />
          FII <Mono size={11} weight={600} color={dirColor(sumF)}>{(sumF >= 0 ? '+' : '') + sumF.toLocaleString('en-IN')}</Mono>
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: T.navy }} />
          DII <Mono size={11} weight={600} color={dirColor(sumD)}>{(sumD >= 0 ? '+' : '') + sumD.toLocaleString('en-IN')}</Mono>
        </span>
        <span style={{ color: T.faint }}>cumulative over window · provisional</span>
      </div>
    </Card>
  );
}

interface MetricSpec {
  id: string; label: string; color: string; series: number[];
  def: CardMode; barStyle: 'bottom' | 'top'; span: number;
  desc: string;
}

export function BreadthTab({ D, cardMode, setCard, range, setRange }: {
  D: MarketData;
  cardMode: Record<string, CardMode>;
  setCard: (id: string, m: CardMode) => void;
  range: BreadthRange;
  setRange: (r: BreadthRange) => void;
}) {
  const dates = D.dates || [];
  const asOf = dates.length ? fmtDayYear(dates[dates.length - 1]) : null;
  const sessions = RANGES.find(r => r.id === range)?.sessions ?? 21;

  const total = D.advances + D.declines;
  const pctAdv = D.advances / total * 100;
  const f = Math.max(0, Math.min(1, pctAdv / 100));
  const gaugeColor = pctAdv >= 58 ? T.up : pctAdv >= 46 ? T.amber : T.down;
  const gaugeTrack = arc(110, 100, 82, 215, 505);
  const gaugeValue = arc(110, 100, 82, 215, 215 + 290 * f);

  const netHighs = D.newHighs - D.newLows;

  // EMA participation: today's reading plus how it moved across the window.
  const emaCharts = ([
    ['20 EMA', D.emaVals.e20, D.emaHist.e20],
    ['50 EMA', D.emaVals.e50, D.emaHist.e50],
    ['200 EMA', D.emaVals.e200, D.emaHist.e200],
  ] as [string, number, number[]][]).map(([label, value, hist]) => {
    const { vals, n } = windowOf(hist, sessions);
    const p = areaLine(vals, 300, 54, 5, 6);
    const delta = value - vals[0];
    return { label, value: value.toFixed(1), line: p.line, area: p.area, delta, n };
  });

  const riskScore = Math.round(0.4 * pctAdv + 0.28 * D.emaVals.e50 + 0.32 * D.emaVals.e200);
  const riskOn = riskScore >= 58, neutral = riskScore >= 46 && riskScore < 58;
  const regimeTag = riskOn ? 'RISK-ON' : neutral ? 'NEUTRAL' : 'RISK-OFF';
  const regimeColor = riskOn ? T.up : neutral ? T.amber : T.down;
  const regimeBg = riskOn ? T.upSoft : neutral ? T.amberSoft : T.downSoft;
  const emaAbove = [D.emaVals.e20, D.emaVals.e50, D.emaVals.e200].filter(v => v >= 50).length;
  const regimeStats = emaAbove + ' of 3 EMA gauges above 50 · net new highs ' + (netHighs >= 0 ? '+' : '') + netHighs + ' · ' + pctAdv.toFixed(0) + '% advancing';
  const regimeVerdict = riskOn ? 'favourable — press winners, add on breakouts' : neutral ? 'mixed — be selective, normal size' : 'unfavourable — minimum size or stand aside';

  const advWidth = Math.round(D.advances / (total + D.unchanged) * 100) + '%';
  const decWidth = Math.round(D.declines / (total + D.unchanged) * 100) + '%';

  const ad = windowOf(D.adDaily, sessions);
  const nh = windowOf(D.nhDaily, sessions);
  const flowN = Math.min(sessions, D.flows.fii.length);
  const momN = D.series.newHighs.length;

  const specs: MetricSpec[] = [
    // Net highs − lows is not repeated here: the signed chart above already
    // plots it, and that one follows the history-window selector.
    { id: 'newHighs', label: 'New Highs', color: T.up, series: D.series.newHighs, def: 'bar', barStyle: 'bottom', span: 6, desc: 'Stocks closing at a fresh 52-week high that session' },
    { id: 'newLows', label: 'New Lows', color: T.down, series: D.series.newLows, def: 'bar', barStyle: 'top', span: 6, desc: 'Stocks closing at a fresh 52-week low that session' },
    { id: 'up20', label: 'Up 20%+ in 5D', color: T.navy, series: D.series.up20, def: 'area', barStyle: 'bottom', span: 6, desc: 'Stocks up 20%+ over the trailing 5 sessions, counted daily' },
    { id: 'up30', label: 'Up 30%+ in 5D', color: T.navy, series: D.series.up30, def: 'area', barStyle: 'bottom', span: 6, desc: 'Stocks up 30%+ over the trailing 5 sessions, counted daily' },
    { id: 'up4vol', label: 'Up 4%+ on Volume', color: T.up, series: D.series.up4vol, def: 'area', barStyle: 'bottom', span: 6, desc: 'Gained 4%+ on volume above its own 20-session average' },
    { id: 'down4vol', label: 'Down 4%+ on Volume', color: T.down, series: D.series.down4vol, def: 'area', barStyle: 'bottom', span: 6, desc: 'Fell 4%+ on volume above its own 20-session average' },
  ];

  return (
    <>
      {/* Window control — one place that sets the period for every history chart. */}
      <Card style={{ marginTop: 18, padding: '12px 18px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <div>
          <Label>History window</Label>
          <div style={{ fontSize: 11.5, color: T.faint, marginTop: 3 }}>
            Applies to the EMA, advance/decline and net-high charts below
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4, background: T.borderSoft, borderRadius: 10, padding: 3 }}>
          {RANGES.map(r => {
            const active = range === r.id;
            const has = Math.min(r.sessions, D.adDaily.length);
            return (
              <button
                key={r.id}
                onClick={() => setRange(r.id)}
                title={r.sessions + ' sessions'}
                style={{
                  appearance: 'none', border: 'none', cursor: 'pointer', borderRadius: 8,
                  padding: '6px 14px', fontFamily: T.sans, fontSize: 12.5, fontWeight: 600,
                  background: active ? T.card : 'transparent',
                  color: active ? T.ink : T.muted,
                  boxShadow: active ? T.shadow : 'none',
                }}
              >
                {r.label}
                <span style={{ fontSize: 10, color: T.faint, fontWeight: 500 }}> · {has}d</span>
              </button>
            );
          })}
        </div>
        <div style={{ flex: 1 }} />
        {asOf && (
          <div style={{ fontSize: 11.5, color: T.muted }}>
            Data as of <Mono size={11.5} weight={600}>{asOf}</Mono> close
          </div>
        )}
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1.45fr', gap: 16, marginTop: 16 }}>
        <Card style={{ padding: '22px 24px' }}>
          <Label>Market Pulse</Label>
          <Serif size={25} style={{ marginTop: 4 }}>Breadth Thrust</Serif>
          <div style={{ fontSize: 11.5, color: T.faint, marginTop: 4 }}>
            Single session snapshot{asOf ? ' · ' + asOf : ''} — not an average
          </div>
          <div style={{ position: 'relative', width: 220, margin: '10px auto 6px' }}>
            <svg viewBox="0 0 220 200" style={{ width: 220, height: 200, display: 'block' }}>
              <path d={gaugeTrack} fill="none" stroke={T.borderSoft} strokeWidth={16} strokeLinecap="round" />
              <path d={gaugeValue} fill="none" stroke={gaugeColor} strokeWidth={16} strokeLinecap="round" />
            </svg>
            <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', paddingBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'baseline' }}>
                <span style={{ fontFamily: T.serif, fontSize: 50, fontWeight: 600, letterSpacing: '-0.03em', color: T.ink, lineHeight: 1 }}>{pctAdv.toFixed(1)}</span>
                <span style={{ fontSize: 19, fontWeight: 600, color: T.faint }}>%</span>
              </div>
              <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.12em', color: T.faint, marginTop: 8 }}>OF UNIVERSE ADVANCING</div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 10, marginTop: 4 }}>
            <span style={{ fontFamily: T.serif, fontSize: 28, fontWeight: 600, color: T.up }}>{D.advances.toLocaleString('en-IN')}</span>
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: T.faint }}>ADVANCES · DECLINES</span>
            <span style={{ fontFamily: T.serif, fontSize: 28, fontWeight: 600, color: T.down }}>{D.declines.toLocaleString('en-IN')}</span>
          </div>
          <div style={{ display: 'flex', gap: 3, height: 8, marginTop: 12 }}>
            <div style={{ width: advWidth, background: T.up, borderRadius: 99, opacity: 0.85 }} />
            <div style={{ width: decWidth, background: T.down, borderRadius: 99, opacity: 0.85 }} />
          </div>
          <div style={{ textAlign: 'center', fontSize: 11.5, color: T.faint, marginTop: 8 }}>Universe {D.universe.toLocaleString('en-IN')} NSE stocks</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: T.text, background: T.borderSoft, borderRadius: 99, padding: '5px 12px' }}>+20% in 5D · <Mono size={12} color={T.up} weight={600}>{D.series.up20[momN - 1]}</Mono></div>
            <div style={{ fontSize: 12, fontWeight: 600, color: T.text, background: T.borderSoft, borderRadius: 99, padding: '5px 12px' }}>4% vol moves · <Mono size={12} color={T.up} weight={600}>{D.volUp}▲</Mono> <Mono size={12} color={T.down} weight={600}>{D.volDn}▼</Mono></div>
          </div>
        </Card>

        <div style={{ display: 'grid', gridTemplateRows: 'repeat(3, 1fr)', gap: 12 }}>
          {emaCharts.map(e => (
            <Card key={e.label} style={{ padding: '14px 18px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: T.text }}>% above {e.label}</span>
                  <WindowNote>Share of stocks closing above their own {e.label} · today&apos;s reading, {e.n}-session trend</WindowNote>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <Mono size={18} weight={600} color={T.navy}>{e.value}%</Mono>
                  <div style={{ fontSize: 10.5, color: dirColor(e.delta), fontWeight: 600 }}>
                    {(e.delta >= 0 ? '+' : '') + e.delta.toFixed(1)} pts
                  </div>
                </div>
              </div>
              <svg viewBox="0 0 300 54" preserveAspectRatio="none" style={{ width: '100%', height: 54, marginTop: 6, display: 'block', overflow: 'visible' }}>
                <path d="M0 27 H300" stroke={T.borderSoft} strokeWidth={1} strokeDasharray="3 3" />
                <path d={e.area} fill={T.navySoft} stroke="none" />
                <path d={e.line} fill="none" stroke={T.navy} strokeWidth={1.75} strokeLinejoin="round" />
              </svg>
              <DateAxis dates={dates} n={e.n} />
            </Card>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginTop: 16 }}>
        <SignedChart
          title="Daily Advances − Declines"
          badge={'latest ' + (ad.vals[ad.n - 1] >= 0 ? '+' : '') + ad.vals[ad.n - 1]}
          vals={ad.vals} dates={dates} n={ad.n}
          note={'Net advancing stocks per session · ' + windowNote(dates, ad.n)}
        />
        <SignedChart
          title="Net Highs − Lows"
          badge={'NNH ' + (nh.vals[nh.n - 1] >= 0 ? '+' : '') + nh.vals[nh.n - 1]}
          vals={nh.vals} dates={dates} n={nh.n}
          note={'52-week highs minus lows per session · ' + windowNote(dates, nh.n)}
        />
        <FlowsChart
          fii={D.flows.fii.slice(-flowN)}
          dii={D.flows.dii.slice(-flowN)}
          dates={D.flowDates && D.flowDates.length ? D.flowDates : dates}
        />
      </div>

      <Card style={{ marginTop: 16, padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.06em', color: regimeColor, background: regimeBg, borderRadius: 99, padding: '5px 12px' }}>{regimeTag}</span>
          <Mono size={15} weight={600}>{riskScore}<span style={{ color: T.faint, fontSize: 12 }}>/100</span></Mono>
        </div>
        <div style={{ fontSize: 13.5, color: T.text, flex: 1 }}>{regimeStats} — <span style={{ fontWeight: 700, color: regimeColor }}>{regimeVerdict}</span></div>
      </Card>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 26, flexWrap: 'wrap' }}>
        <Serif size={17}>Momentum &amp; Participation</Serif>
        <div style={{ fontSize: 12, color: T.faint }}>
          daily counts across the universe · fixed {momN}-session window
          {rangeLabel(dates, momN) ? ' · ' + rangeLabel(dates, momN) : ''}
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 14, marginTop: 14 }}>
        {specs.map(spec => {
          const m = cardMode[spec.id] || spec.def;
          const n = spec.series.length;
          const last = spec.series[n - 1];
          const value = String(Math.round(last));
          let area = '', line = '', bars = '';
          if (m === 'area') {
            const p = areaLine(spec.series, 300, 104, 8, 10); area = p.area; line = p.line;
          } else if (spec.barStyle === 'top') {
            bars = barsTop(spec.series, 300, 104, 10);
          } else {
            bars = barsBottom(spec.series, 300, 104, 10);
          }
          const valueColor = spec.color;
          const fill = spec.color === T.navy ? T.navySoft : spec.color === T.up ? T.upSoft : T.downSoft;
          const avg = spec.series.reduce((a, b) => a + b, 0) / Math.max(1, n);
          return (
            <Card key={spec.id} style={{ gridColumn: 'span ' + spec.span, padding: '16px 18px 12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>{spec.label}</div>
                  <WindowNote>{spec.desc}</WindowNote>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                  <div style={{ display: 'flex', gap: 2, background: T.borderSoft, borderRadius: 8, padding: 2 }}>
                    <button onClick={() => setCard(spec.id, 'area')} title="Line" style={{ appearance: 'none', border: 'none', cursor: 'pointer', borderRadius: 6, padding: '3px 6px', background: m === 'area' ? T.card : 'transparent', color: m === 'area' ? T.ink : T.faint, fontSize: 12, lineHeight: 1 }}>⌁</button>
                    <button onClick={() => setCard(spec.id, 'bar')} title="Bar" style={{ appearance: 'none', border: 'none', cursor: 'pointer', borderRadius: 6, padding: '3px 6px', background: m === 'bar' ? T.card : 'transparent', color: m === 'bar' ? T.ink : T.faint, fontSize: 12, lineHeight: 1 }}>☰</button>
                  </div>
                  <div style={{ fontFamily: T.serif, fontSize: 23, fontWeight: 600, letterSpacing: '-0.02em', color: valueColor }}>{value}</div>
                </div>
              </div>
              <svg viewBox="0 0 300 104" preserveAspectRatio="none" style={{ width: '100%', height: 104, marginTop: 10, display: 'block', overflow: 'visible' }}>
                <path d="M0 34 H300 M0 68 H300" stroke={T.borderSoft} strokeWidth={1} />
                <path d={area} fill={fill} stroke="none" />
                <path d={line} fill="none" stroke={spec.color} strokeWidth={1.75} strokeLinejoin="round" />
                <path d={bars} fill={spec.color} />
              </svg>
              <DateAxis dates={dates} n={n} />
              <div style={{ fontSize: 10.5, color: T.faint, marginTop: 5 }}>
                latest <Mono size={10.5} weight={600} color={valueColor}>{Math.round(last)}</Mono>
                {' '}vs {n}-session average <Mono size={10.5} weight={600}>{avg.toFixed(0)}</Mono>
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
