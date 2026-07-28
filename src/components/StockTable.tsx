import type { ReactNode } from 'react';
import { T, dirColor } from '../theme';
import { Mono, Tag } from './ui';
import type { Stock } from '../lib/data';
import { fundamentals } from '../lib/fundamentals';
import { fmtPct, fmtPrice } from '../lib/format';

const GRID = '34px 1.1fr 1.1fr 0.85fr 0.6fr 0.6fr 0.6fr 0.6fr 0.75fr 0.85fr';

export function stockTag(s: Stock) {
  if (s.isATH) return <Tag color={T.card} bg={T.ink}>ATH</Tag>;
  if (s.is52) return <Tag color={T.up} bg={T.upSoft}>52W HIGH</Tag>;
  if (s.wkBreak) return <Tag color={T.amber} bg={T.amberSoft}>BREAKOUT</Tag>;
  if (s.distATH < 10) return <Tag color={T.navy} bg={T.navySoft}>NEAR HIGH</Tag>;
  return <Tag color={T.muted} bg={T.borderSoft}>OFF HIGHS</Tag>;
}

export function TableShell({ children }: { children: ReactNode }) {
  return (
    <div style={{ background: T.card, border: '1px solid ' + T.border, borderRadius: T.radius, overflow: 'hidden', boxShadow: T.shadow }}>
      {children}
    </div>
  );
}

export function TableHead({ grid, cols }: { grid: string; cols: (string | [string, 'right'])[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: grid, gap: 14, padding: '12px 22px', background: T.cardAlt, borderBottom: '1px solid ' + T.border, fontSize: 10.5, fontWeight: 700, color: T.faint, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
      {cols.map((c, i) => Array.isArray(c)
        ? <div key={i} style={{ textAlign: 'right' }}>{c[0]}</div>
        : <div key={i}>{c}</div>)}
    </div>
  );
}

export function Footnote({ children }: { children: ReactNode }) {
  return <div style={{ padding: '12px 22px', fontSize: 12, color: T.faint }}>{children}</div>;
}

export function StockTable({ stocks, watch, toggle, onOpen, footnote }: {
  stocks: Stock[];
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
  footnote: string;
}) {
  return (
    <TableShell>
      <TableHead grid={GRID} cols={['', 'Symbol', 'Sector', ['Price', 'right'], ['1D %', 'right'], ['1W %', 'right'], ['P/E', 'right'], ['ROE', 'right'], ['From ATH', 'right'], ['Tag', 'right']]} />
      {stocks.map(s => {
        const f = fundamentals(s.sym, s.sector, s.price);
        const starred = !!watch[s.sym];
        return (
          <div
            key={s.sym}
            onClick={() => onOpen(s.sym)}
            style={{ display: 'grid', gridTemplateColumns: GRID, gap: 14, alignItems: 'center', padding: '10px 22px', borderBottom: '1px solid ' + T.borderSoft, fontSize: 13.5, cursor: 'pointer' }}
            onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = T.cardAlt; }}
            onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
          >
            <button onClick={e => { e.stopPropagation(); toggle(s.sym); }} title="Watchlist" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, color: starred ? T.amber : T.border, padding: 0, lineHeight: 1 }}>{starred ? '★' : '☆'}</button>
            <Mono size={13} weight={600}>{s.sym}</Mono>
            <div style={{ color: T.muted, fontSize: 12.5 }}>{s.sector}</div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5}>{fmtPrice(s.price)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.chg1d)}>{fmtPct(s.chg1d)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.chg1w)}>{fmtPct(s.chg1w)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={T.muted}>{f.pe.toFixed(0)}x</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={f.roe >= 15 ? T.up : T.muted}>{f.roe.toFixed(0)}%</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={T.muted}>{s.distATH < 0.05 ? '0.0%' : '-' + s.distATH.toFixed(1) + '%'}</Mono></div>
            <div style={{ textAlign: 'right' }}>{stockTag(s)}</div>
          </div>
        );
      })}
      <Footnote>{footnote}</Footnote>
    </TableShell>
  );
}
