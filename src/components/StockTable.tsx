import type { ReactNode } from 'react';
import { T, dirColor } from '../theme';
import { Mono, Tag } from './ui';
import type { Stock } from '../lib/data';
import { fmtPct, fmtPrice } from '../lib/format';
import { useSort, sortLabel } from '../lib/sort';
import type { SortSpec, SortState } from '../lib/sort';

const GRID = '34px 1.1fr 1.1fr 0.85fr 0.6fr 0.6fr 0.75fr 1.5fr';

/**
 * Below this width the eight columns stop being readable, so the table keeps
 * its full width and scrolls sideways inside its own card instead of letting
 * the columns collapse into each other. The last column is sized to hold a
 * position tag and a TREND BREAK side by side without wrapping the row.
 */
export const TABLE_MIN_WIDTH = 820;

/**
 * Where price is, and — separately — whether a level just gave way.
 *
 * These are two independent facts, not five rungs of one ladder. A stock can be
 * running into its highs *and* have just cleared a two-year downtrend line, and
 * collapsing that into a single most-specific label was what made the Weekly
 * breakouts tab look like it was arguing with itself: a row selected for its
 * momentum wore a tag that never mentioned momentum.
 *
 * So the position tag always renders, and TREND BREAK sits beside it when there
 * is one. The position tag stays rightmost so the column keeps a clean edge to
 * scan down, with the rarer pill jutting left only on the rows that earn it.
 */
export function stockTag(s: Stock) {
  const position = s.isATH ? <Tag color={T.card} bg={T.ink}>ATH</Tag>
    : s.is52 ? <Tag color={T.up} bg={T.upSoft}>52W HIGH</Tag>
    : s.wkBreak ? <Tag color={T.amber} bg={T.amberSoft}>BREAKOUT</Tag>
    : s.distATH < 10 ? <Tag color={T.navy} bg={T.navySoft}>NEAR HIGH</Tag>
    : <Tag color={T.muted} bg={T.borderSoft}>OFF HIGHS</Tag>;
  if (!s.trendBreak) return position;
  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 5, alignItems: 'center', justifyContent: 'flex-end' }}>
      <Tag color={T.brand700} bg={T.brand50}>TREND BREAK</Tag>
      {position}
    </span>
  );
}

/**
 * Card around a grid table. The inner track holds the table at its natural
 * width and scrolls horizontally when the viewport is narrower, which keeps
 * every row aligned to the header instead of squeezing columns to zero.
 */
export function TableShell({ children, minWidth = TABLE_MIN_WIDTH }: { children: ReactNode; minWidth?: number }) {
  return (
    <div style={{ background: T.card, borderRadius: T.radius, overflow: 'hidden', boxShadow: T.shadow + ', inset 0 0 0 1px ' + T.borderSoft }}>
      <div className="table-scroll">
        <div style={{ minWidth }}>
          {children}
        </div>
      </div>
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
                  // Vertical padding (and the negative margin that cancels it)
                  // grows the tap target to ~30px without moving the label.
                  appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer',
                  padding: '9px 0', margin: '-9px 0',
                  fontFamily: T.sans, color: active ? T.ink : T.faint,
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

// The trendline pool swaps one column rather than adding one: every stock in it
// is by definition well off its highs, so "From ATH" is the least informative
// cell on the row, and holding the count at eight keeps the phone layout put.
const TREND_COLS: HeadCol[] = COLS.map(c =>
  c.key === 'distATH' ? { key: 'trendWeeks', label: 'Line broken', align: 'right' as const } : c);

// Position sets the rung; a broken line lifts a stock half a step above its
// peers on that rung rather than displacing it to one of its own.
const tagRank = (s: Stock) => {
  const position = s.isATH ? 5 : s.is52 ? 4 : s.wkBreak ? 3 : s.distATH < 10 ? 2 : 1;
  return s.trendBreak ? position + 0.5 : position;
};

const SPECS: SortSpec<Stock>[] = [
  { key: 'sym', value: s => s.sym, first: 'asc' },
  { key: 'sector', value: s => s.sector, first: 'asc' },
  { key: 'price', value: s => s.price },
  { key: 'chg1d', value: s => s.chg1d },
  { key: 'chg1w', value: s => s.chg1w },
  { key: 'distATH', value: s => s.distATH, first: 'asc' },
  { key: 'trendWeeks', value: s => s.trendWeeks ?? 0 },
  { key: 'tag', value: s => tagRank(s) },
];

/** "A 151-week descending trendline broke in the week of 2026-07-27, on 2.4x …" */
export function breakSummary(s: Stock): string {
  if (!s.trendBreak) return '';
  const vol = s.breakVol ? ', on ' + s.breakVol.toFixed(1) + 'x the prior ten weeks of volume' : '';
  const age = s.breakWeeks === 0 ? 'this week' : 'in the week of ' + s.breakDate;
  return `A ${s.trendWeeks}-week descending trendline, touched ${s.trendTouches} times, broke ${age}${vol}. `
    + `It gave way at ${s.breakLevel?.toFixed(2)} — the level the move has to hold.`;
}

export function StockTable({ stocks, watch, toggle, onOpen, footnote, initialSort, dense, trend }: {
  stocks: Stock[];
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
  footnote: string;
  initialSort?: SortState;
  dense?: boolean;
  /** Show the broken-line column in place of "From ATH". */
  trend?: boolean;
}) {
  const cols = trend ? TREND_COLS : COLS;
  const { sorted, sort, onSort } = useSort(stocks, SPECS, initialSort || { key: 'chg1w', dir: 'desc' });
  const pad = dense ? '8px 22px' : '10px 22px';

  return (
    <TableShell>
      <TableHead grid={GRID} cols={cols} sort={sort} onSort={onSort} />
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
            <button onClick={e => { e.stopPropagation(); toggle(s.sym); }} title="Watchlist" aria-label={starred ? 'Remove from watchlist' : 'Add to watchlist'} style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, color: starred ? T.amber : T.border, padding: '7px 6px', margin: '-7px -6px', lineHeight: 1 }}>{starred ? '★' : '☆'}</button>
            <Mono size={13} weight={600}>{s.sym}</Mono>
            <div style={{ color: T.muted, fontSize: 12.5 }}>{s.sector}</div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5}>{fmtPrice(s.price)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.chg1d)}>{fmtPct(s.chg1d)}</Mono></div>
            <div style={{ textAlign: 'right' }}><Mono size={12.5} color={dirColor(s.chg1w)}>{fmtPct(s.chg1w)}</Mono></div>
            {trend ? (
              <div style={{ textAlign: 'right' }} title={breakSummary(s)}>
                <Mono size={12.5}>{s.trendWeeks}w</Mono>
                <Mono size={11} color={T.faint}>{s.breakWeeks === 0 ? ' · now' : ' · ' + s.breakWeeks + 'w ago'}</Mono>
              </div>
            ) : (
              <div style={{ textAlign: 'right' }}><Mono size={12.5} color={T.muted}>{s.distATH < 0.05 ? '0.0%' : '-' + s.distATH.toFixed(1) + '%'}</Mono></div>
            )}
            <div style={{ textAlign: 'right' }}>{stockTag(s)}</div>
          </div>
        );
      })}
      <Footnote>{footnote} · {sortLabel(sort, cols.filter(c => c.key).map(c => ({ key: c.key!, label: c.label })))}</Footnote>
    </TableShell>
  );
}
