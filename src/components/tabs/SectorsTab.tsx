import { useMemo, useState } from 'react';
import { T, dirColor } from '../../theme';
import { Mono, Meter } from '../ui';
import { TableShell, TableHead, Footnote, stockTag } from '../StockTable';
import type { HeadCol } from '../StockTable';
import type { MarketData, Sector, Stock } from '../../lib/data';
import { fmtPct, fmtPrice } from '../../lib/format';
import { useSort, sortLabel } from '../../lib/sort';
import type { SortSpec } from '../../lib/sort';

const GRID = '18px 1.5fr 0.7fr 0.9fr 1.4fr 0.8fr 0.7fr 1.2fr';
const SUB = '34px 1.1fr 0.9fr 0.7fr 0.7fr 0.9fr 0.9fr';

const COLS: HeadCol[] = [
  { label: '' },
  { key: 'name', label: 'Sector' },
  { key: 'count', label: 'Stocks', align: 'right' },
  { key: 'adv', label: 'Adv / Dec', align: 'right' },
  { key: 'dmaPct', label: '% above 50 DMA' },
  { key: 'newHighs', label: 'New highs', align: 'right' },
  { key: 'wk', label: '1W %', align: 'right' },
  { key: 'score', label: 'Strength' },
];

const SPECS: SortSpec<Sector>[] = [
  { key: 'name', value: s => s.name, first: 'asc' },
  { key: 'count', value: s => s.count },
  { key: 'adv', value: s => s.adv / Math.max(1, s.count) },
  { key: 'dmaPct', value: s => s.dmaPct },
  { key: 'newHighs', value: s => s.newHighs },
  { key: 'wk', value: s => s.wk },
  { key: 'score', value: s => s.score },
];

const SUB_COLS: HeadCol[] = [
  { label: '' },
  { key: 'sym', label: 'Symbol' },
  { key: 'price', label: 'Price', align: 'right' },
  { key: 'chg1d', label: '1D %', align: 'right' },
  { key: 'chg1w', label: '1W %', align: 'right' },
  { key: 'distATH', label: 'From ATH', align: 'right' },
  { key: 'tag', label: 'Tag', align: 'right' },
];

const tagRank = (s: Stock) => s.isATH ? 5 : s.is52 ? 4 : s.wkBreak ? 3 : s.distATH < 10 ? 2 : 1;

const SUB_SPECS: SortSpec<Stock>[] = [
  { key: 'sym', value: s => s.sym, first: 'asc' },
  { key: 'price', value: s => s.price },
  { key: 'chg1d', value: s => s.chg1d },
  { key: 'chg1w', value: s => s.chg1w },
  { key: 'distATH', value: s => s.distATH, first: 'asc' },
  { key: 'tag', value: s => tagRank(s) },
];

const subLabels = SUB_COLS.filter(c => c.key).map(c => ({ key: c.key!, label: c.label }));
const headLabels = COLS.filter(c => c.key).map(c => ({ key: c.key!, label: c.label }));

type Side = 'all' | 'adv' | 'dec';

// Expanded panel: the actual constituents of one sector, split into advancing
// and declining, each sortable on its own.
function SectorDetail({ stocks, watch, toggle, onOpen }: {
  stocks: Stock[];
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
}) {
  const [side, setSide] = useState<Side>('all');
  const advList = stocks.filter(s => s.chg1d >= 0);
  const decList = stocks.filter(s => s.chg1d < 0);
  const pool = side === 'adv' ? advList : side === 'dec' ? decList : stocks;
  const { sorted, sort, onSort } = useSort(pool, SUB_SPECS, { key: 'chg1d', dir: 'desc' });

  const chips: { id: Side; label: string; n: number; color: string }[] = [
    { id: 'all', label: 'All', n: stocks.length, color: T.ink },
    { id: 'adv', label: 'Advancing', n: advList.length, color: T.up },
    { id: 'dec', label: 'Declining', n: decList.length, color: T.down },
  ];

  return (
    <div style={{ background: T.cardAlt, borderBottom: '1px solid ' + T.border, padding: '14px 22px 4px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {chips.map(c => {
          const active = side === c.id;
          return (
            <button
              key={c.id}
              onClick={() => setSide(c.id)}
              style={{
                appearance: 'none', cursor: 'pointer', fontFamily: T.sans, fontSize: 12, fontWeight: 600,
                padding: '5px 11px', borderRadius: 99,
                border: '1px solid ' + (active ? c.color : T.border),
                background: active ? c.color : T.card,
                color: active ? '#fff' : T.text,
              }}
            >
              {c.label} · {c.n}
            </button>
          );
        })}
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', height: 6, width: 160, borderRadius: 99, overflow: 'hidden', background: T.borderSoft }}>
          <div style={{ width: (advList.length / Math.max(1, stocks.length) * 100) + '%', background: T.up, opacity: 0.85 }} />
          <div style={{ width: (decList.length / Math.max(1, stocks.length) * 100) + '%', background: T.down, opacity: 0.85 }} />
        </div>
      </div>

      <div style={{ marginTop: 12, background: T.card, borderRadius: 10, overflow: 'hidden', boxShadow: 'inset 0 0 0 1px ' + T.borderSoft }}>
        <TableHead grid={SUB} cols={SUB_COLS} sort={sort} onSort={onSort} />
        {sorted.map(s => {
          const starred = !!watch[s.sym];
          return (
            <div
              key={s.sym}
              onClick={() => onOpen(s.sym)}
              style={{ display: 'grid', gridTemplateColumns: SUB, gap: 14, alignItems: 'center', padding: '8px 22px', borderBottom: '1px solid ' + T.borderSoft, fontSize: 13, cursor: 'pointer' }}
              onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = T.cardAlt; }}
              onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
            >
              <button onClick={e => { e.stopPropagation(); toggle(s.sym); }} title="Watchlist" style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 14, color: starred ? T.amber : T.border, padding: 0, lineHeight: 1 }}>{starred ? '★' : '☆'}</button>
              <Mono size={12.5} weight={600}>{s.sym}</Mono>
              <div style={{ textAlign: 'right' }}><Mono size={12}>{fmtPrice(s.price)}</Mono></div>
              <div style={{ textAlign: 'right' }}><Mono size={12} color={dirColor(s.chg1d)}>{fmtPct(s.chg1d)}</Mono></div>
              <div style={{ textAlign: 'right' }}><Mono size={12} color={dirColor(s.chg1w)}>{fmtPct(s.chg1w)}</Mono></div>
              <div style={{ textAlign: 'right' }}><Mono size={12} color={T.muted}>{s.distATH < 0.05 ? '0.0%' : '-' + s.distATH.toFixed(1) + '%'}</Mono></div>
              <div style={{ textAlign: 'right' }}>{stockTag(s)}</div>
            </div>
          );
        })}
        {sorted.length === 0 && (
          <div style={{ padding: '18px 22px', fontSize: 12.5, color: T.faint }}>
            No {side === 'adv' ? 'advancing' : 'declining'} stocks in this sector today.
          </div>
        )}
      </div>
      <div style={{ padding: '10px 2px', fontSize: 11.5, color: T.faint }}>
        {sorted.length} of {stocks.length} tracked constituents · {sortLabel(sort, subLabels)}
      </div>
    </div>
  );
}

export function SectorsTab({ D, watch, toggle, onOpen }: {
  D: MarketData;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const { sorted, sort, onSort } = useSort(D.sectors, SPECS, { key: 'score', dir: 'desc' });

  // Tracked constituents grouped by sector, so the drill-down lists real names.
  const bySector = useMemo(() => {
    const m: Record<string, Stock[]> = {};
    D.stocks.forEach(s => { (m[s.sector] = m[s.sector] || []).push(s); });
    return m;
  }, [D.stocks]);

  return (
    <div style={{ marginTop: 18 }}>
      <TableShell>
        <TableHead grid={GRID} cols={COLS} sort={sort} onSort={onSort} />
        {sorted.map(s => {
          const expanded = open === s.name;
          const members = bySector[s.name] || [];
          return (
            <div key={s.name}>
              <div
                onClick={() => setOpen(expanded ? null : s.name)}
                title={expanded ? 'Collapse' : 'Show constituents'}
                style={{ display: 'grid', gridTemplateColumns: GRID, gap: 14, alignItems: 'center', padding: '12px 22px', borderBottom: '1px solid ' + T.borderSoft, fontSize: 13.5, cursor: 'pointer', background: expanded ? T.cardAlt : 'transparent' }}
                onMouseEnter={e => { if (!expanded) (e.currentTarget as HTMLDivElement).style.background = T.cardAlt; }}
                onMouseLeave={e => { if (!expanded) (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
              >
                <div style={{ color: T.faint, fontSize: 9, transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }}>▶</div>
                <div style={{ fontWeight: 600, color: T.ink }}>{s.name}</div>
                <div style={{ textAlign: 'right' }}><Mono size={12.5} color={T.muted}>{s.count}</Mono></div>
                <div style={{ textAlign: 'right' }}>
                  <Mono size={12.5} color={T.up}>{s.adv}</Mono>
                  <Mono size={12.5} color={T.faint}> / </Mono>
                  <Mono size={12.5} color={T.down}>{s.dec}</Mono>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Meter pct={s.dmaPct} color={s.dmaPct >= 50 ? T.navy : T.down} height={6} />
                  <Mono size={12} weight={600} style={{ width: 36, textAlign: 'right', display: 'inline-block' }}>{s.dmaPct}%</Mono>
                </div>
                <div style={{ textAlign: 'right' }}><Mono size={12.5} weight={600}>{s.newHighs}</Mono></div>
                <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.wk)}>{fmtPct(s.wk)}</Mono></div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Meter pct={s.score} color={T.amber} height={6} />
                  <Mono size={12} weight={600} style={{ width: 24, textAlign: 'right', display: 'inline-block' }}>{s.score}</Mono>
                </div>
              </div>
              {expanded && <SectorDetail stocks={members} watch={watch} toggle={toggle} onOpen={onOpen} />}
            </div>
          );
        })}
        <Footnote>
          Click any sector to see its constituents split into advancing and declining · every column sorts · {sortLabel(sort, headLabels)}
        </Footnote>
      </TableShell>
    </div>
  );
}
