import type { ReactNode } from 'react';
import { T, dirColor } from '../theme';
import { Mono, Tag } from './ui';
import type { Stock } from '../lib/data';
import { fmtPct, fmtPrice } from '../lib/format';
import { useSort, sortLabel } from '../lib/sort';
import type { SortSpec, SortState } from '../lib/sort';

const GRID = '34px 1.1fr 1.1fr 0.85fr 0.6fr 0.6fr 0.75fr 0.85fr';

export function stockTag(s: Stock) {
  if (s.isATH) return <Tag color={T.card} bg={T.ink}>ATH</Tag>;
  if (s.is52) return <Tag color={T.up} bg={T.upSoft}>52W HIGH</Tag>;
  if (s.wkBreak) return <Tag color={T.amber} bg={T.amberSoft}>BREAKOUT</Tag>;
  if (s.distATH < 10) return <Tag color={T.navy} bg={T.navySoft}>NEAR HIGH</Tag>;
  return <Tag color={T.muted} bg={T.borderSoft}>OFF HIGHS</Tag>;
}

export function TableShell({ children }: { children: ReactNode }) {
  return (
    <div style={{ background: T.card, borderRadius: T.radius, overflow: 'hidden', boxShadow: T.shadow + ', inset 0 0 0 1px ' + T.borderSoft }}>
      {children}
    </div>
  );
}

export interface HeadCol {
  key?: string;          // omit to make the column non-sortable
  label: string;
  align?: 'right';
}

// Sortable header. Columns carrying a `key` become click targets; the active
// one shows its direction. Pass sort/onSort from useSort().
export function TableHead({ grid, cols, sort, onSort }: {
  grid: string;
  cols: HeadCol[];
  sort?: SortState;
  onSort?: (key: string) => void;
}) {
  const base = { fontSize: 10, fontWeight: 700 as const, letterSpacing: '0.08em', textTransform: 'uppercase' as const };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: grid, gap: 14, padding: '11px 22px', background: T.cardAlt, borderBottom: '1px solid ' + T.border, color: T.faint, ...base }}>
      {cols.map((c, i) => {
        const sortable = !!(c.key && onSort);
        const active = !!(c.key && sort && sort.key === c.key);
        const arrow = active ? (sort!.dir === 'asc' ? '▲' : '▼') : '↕';
        return (
          <div key={i} style={{ textAlign: c.align === 'right' ? 'right' : 'left', minWidth: 0 }}>
            {sortable ? (
              <button
                onClick={() => onSort!(c.key!)}
                title={'Sort by ' + c.label}
                style={{
                  appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer',
                  padding: 0, fontFamily: T.sans, color: active ? T.ink : T.faint,
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  flexDirection: c.align === 'right' ? 'row-reverse' : 'row',
                  ...base,
                }}
              >
                <span>{c.label}</span>
                <span style={{ fontSize: 8, opacity: active ? 1 : 0.45, lineHeight: 1 }}>{arrow}</span>
              </button>
            ) : c.label}
          </div>
        );
      })}
    </div>
  );
}

export function Footnote({ children }: { children: ReactNode }) {
  return <div style={{ padding: '12px 22px', fontSize: 12, color: T.faint }}>{children}</div>;
}

const COLS: HeadCol[] = [
  { label: '' },
  { key: 'sym', label: 'Symbol' },
  { key: 'sector', label: 'Sector' },
  { key: 'price', label: 'Price', align: 'right' },
  { key: 'chg1d', label: '1D %', align: 'right' },
  { key: 'chg1w', label: '1W %', align: 'right' },
  { key: 'distATH', label: 'From ATH', align: 'right' },
  { key: 'tag', label: 'Tag', align: 'right' },
];

const tagRank = (s: Stock) => s.isATH ? 5 : s.is52 ? 4 : s.wkBreak ? 3 : s.distATH < 10 ? 2 : 1;

const SPECS: SortSpec<Stock>[] = [
  { key: 'sym', value: s => s.sym, first: 'asc' },
  { key: 'sector', value: s => s.sector, first: 'asc' },
  { key: 'price', value: s => s.price },
  { key: 'chg1d', value: s => s.chg1d },
  { key: 'chg1w', value: s => s.chg1w },
  { key: 'distATH', value: s => s.distATH, first: 'asc' },
  { key: 'tag', value: s => tagRank(s) },
];

export function StockTable({ stocks, watch, toggle, onOpen, footnote, initialSort, dense }: {
  stocks: Stock[];
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
  footnote: string;
  initialSort?: SortState;
  dense?: boolean;
}) {
  const { sorted, sort, onSort } = useSort(stocks, SPECS, initialSort || { key: 'chg1w', dir: 'desc' });
  const pad = dense ? '8px 22px' : '10px 22px';

  return (
    <TableShell>
      <TableHead grid={GRID} cols={COLS} sort={sort} onSort={onSort} />
      {sorted.map(s => {
        const starred = !!watch[s.sym];
        return (
          <div
            key={s.sym}
            onClick={() => onOpen(s.sym)}
            style={{ display: 'grid', gridTemplateColumns: GRID, gap: 14, alignItems: 'center', padding: pad, borderBottom: '1px solid ' + T.borderSoft, fontSize: 13.5, cursor: 'pointer' }}
            onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = T.cardAlt; }}
            onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
          >
            <button onClick={e => { e.stopPropagation(); toggle(s.sym); }} title="Watchlist" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, color: starred ? T.amber : T.border, padding: 0, lineHeight: 1 }}>{starred ? '★' : '☆'}</button>
            <Mono size={13} weight={600}>{s.sym}</Mono>
            <div style={{ color: T.muted, fontSize: 12.5 }}>{s.sector}</div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5}>{fmtPrice(s.price)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.chg1d)}>{fmtPct(s.chg1d)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.chg1w)}>{fmtPct(s.chg1w)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={T.muted}>{s.distATH < 0.05 ? '0.0%' : '-' + s.distATH.toFixed(1) + '%'}</Mono></div>
            <div style={{ textAlign: 'right' }}>{stockTag(s)}</div>
          </div>
        );
      })}
      <Footnote>{footnote} · {sortLabel(sort, COLS.filter(c => c.key).map(c => ({ key: c.key!, label: c.label })))}</Footnote>
    </TableShell>
  );
}
