import { useMemo, useState } from 'react';
import { T, dirColor } from '../../theme';
import { Card, Label, Mono, Meter } from '../ui';
import { TableShell, TableHead, Footnote } from '../StockTable';
import type { HeadCol } from '../StockTable';
import type { MarketData, Stock } from '../../lib/data';
import { fmtPct, fmtPrice } from '../../lib/format';
import { useSort, sortLabel } from '../../lib/sort';
import type { SortSpec } from '../../lib/sort';

const GRID = '34px 1.1fr 1fr 0.8fr 0.7fr 1.3fr 1.1fr';

const ALL = 'All sectors';

function ddBucket(d: number) {
  if (d < 3) return { key: 'high', label: 'AT / NEAR HIGH', color: T.up };
  if (d < 10) return { key: 'dip', label: 'MILD DIP', color: T.navy };
  if (d < 20) return { key: 'corr', label: 'CORRECTION', color: T.amber };
  if (d < 40) return { key: 'bear', label: 'BEAR -20 TO -40', color: T.down };
  return { key: 'deep', label: 'DEEP >40 · BASING', color: T.ink };
}

const COLS: HeadCol[] = [
  { label: '' },
  { key: 'sym', label: 'Symbol' },
  { key: 'sector', label: 'Sector' },
  { key: 'price', label: 'Price', align: 'right' },
  { key: 'chg1d', label: '1D %', align: 'right' },
  { key: 'distATH', label: 'Fall from ATH' },
  { key: 'status', label: 'Status', align: 'right' },
];

const SPECS: SortSpec<Stock>[] = [
  { key: 'sym', value: s => s.sym, first: 'asc' },
  { key: 'sector', value: s => s.sector, first: 'asc' },
  { key: 'price', value: s => s.price },
  { key: 'chg1d', value: s => s.chg1d },
  { key: 'distATH', value: s => s.distATH },
  { key: 'status', value: s => s.distATH },
];

const headLabels = COLS.filter(c => c.key).map(c => ({ key: c.key!, label: c.label }));

const BUCKETS: { key: string; label: string; color: string }[] = [
  { key: 'high', label: 'At / near high', color: T.up },
  { key: 'dip', label: 'Mild dip <10%', color: T.navy },
  { key: 'corr', label: 'Correction 10-20%', color: T.amber },
  { key: 'bear', label: 'Bear 20-40%', color: T.down },
  { key: 'deep', label: 'Deep >40%', color: T.ink },
];

export function DrawdownTab({ D, watch, toggle, onOpen }: {
  D: MarketData;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
}) {
  const [sector, setSector] = useState(ALL);
  const [bucket, setBucket] = useState<string | null>(null);

  // Distribution + sector rollups always describe the sector in view, so the
  // cards above the table stay consistent with what is listed below.
  const inSector = useMemo(
    () => sector === ALL ? D.stocks : D.stocks.filter(s => s.sector === sector),
    [D.stocks, sector],
  );

  const counts: Record<string, number> = { high: 0, dip: 0, corr: 0, bear: 0, deep: 0 };
  inSector.forEach(s => { counts[ddBucket(s.distATH).key]++; });
  const total = Math.max(1, inSector.length);
  const dist = BUCKETS.map(b => ({
    ...b,
    count: counts[b.key],
    pct: Math.round(counts[b.key] / total * 100),
    width: (counts[b.key] / total * 100) + '%',
  }));

  const sectors = useMemo(() => {
    const m: Record<string, number[]> = {};
    D.stocks.forEach(s => { (m[s.sector] = m[s.sector] || []).push(s.distATH); });
    return Object.keys(m).map(name => {
      const arr = m[name];
      return { name, avg: arr.reduce((a, b) => a + b, 0) / arr.length, count: arr.length };
    }).sort((a, b) => a.avg - b.avg);
  }, [D.stocks]);
  const maxAvg = Math.max(...sectors.map(s => s.avg), 1);

  const filtered = useMemo(
    () => bucket ? inSector.filter(s => ddBucket(s.distATH).key === bucket) : inSector,
    [inSector, bucket],
  );
  const { sorted, sort, onSort } = useSort(filtered, SPECS, { key: 'distATH', dir: 'desc' });

  const activeBucket = BUCKETS.find(b => b.key === bucket);
  const filterNote = [
    sector === ALL ? null : sector,
    activeBucket ? activeBucket.label : null,
  ].filter(Boolean).join(' · ');

  return (
    <div style={{ marginTop: 18 }}>
      {/* Filter bar */}
      <Card style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
        <Label style={{ marginRight: 2 }}>Filter</Label>
        <select
          value={sector}
          onChange={e => setSector(e.target.value)}
          style={{ fontFamily: T.sans, fontSize: 13, fontWeight: 500, padding: '7px 11px', borderRadius: 8, border: '1px solid ' + T.border, background: T.card, color: T.text }}
        >
          {[ALL, ...sectors.map(s => s.name)].map(o => (
            <option key={o} value={o}>{o === ALL ? ALL : o + ' · ' + (sectors.find(x => x.name === o)?.count ?? 0)}</option>
          ))}
        </select>

        <div style={{ width: 1, height: 22, background: T.border }} />

        {BUCKETS.map(b => {
          const active = bucket === b.key;
          const n = counts[b.key];
          return (
            <button
              key={b.key}
              onClick={() => setBucket(active ? null : b.key)}
              disabled={n === 0 && !active}
              style={{
                appearance: 'none', cursor: n === 0 && !active ? 'default' : 'pointer',
                fontFamily: T.sans, fontSize: 12, fontWeight: 600, padding: '6px 11px', borderRadius: 99,
                border: '1px solid ' + (active ? b.color : T.border),
                background: active ? b.color : T.card,
                color: active ? '#fff' : n === 0 ? T.faint : T.text,
                opacity: n === 0 && !active ? 0.5 : 1,
              }}
            >
              {b.label} · {n}
            </button>
          );
        })}

        <div style={{ flex: 1 }} />
        {(sector !== ALL || bucket) && (
          <button
            onClick={() => { setSector(ALL); setBucket(null); }}
            style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: T.sans, fontSize: 12.5, fontWeight: 600, color: T.navy }}
          >
            Clear filters
          </button>
        )}
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 16 }}>
        <Card style={{ padding: '20px 22px' }}>
          <Label>How far off all-time highs{sector === ALL ? '' : ' · ' + sector}</Label>
          <div style={{ display: 'flex', height: 10, borderRadius: 99, overflow: 'hidden', marginTop: 16, background: T.borderSoft }}>
            {dist.map(d => <div key={d.label} title={d.label} style={{ width: d.width, background: d.color, opacity: 0.85 }} />)}
          </div>
          <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
            {dist.map(d => {
              const active = bucket === d.key;
              return (
                <div
                  key={d.label}
                  onClick={() => setBucket(active ? null : d.key)}
                  title="Filter the table to this band"
                  style={{ display: 'grid', gridTemplateColumns: '12px 1fr auto auto', alignItems: 'center', gap: 10, cursor: 'pointer', borderRadius: 6, padding: '2px 4px', margin: '0 -4px', background: active ? T.borderSoft : 'transparent' }}
                >
                  <div style={{ width: 10, height: 10, borderRadius: 3, background: d.color, opacity: 0.85 }} />
                  <div style={{ fontSize: 13, color: active ? T.ink : T.text, fontWeight: active ? 600 : 400 }}>{d.label}</div>
                  <Mono size={13} weight={600}>{d.count}</Mono>
                  <Mono size={12} color={T.faint} style={{ width: 34, textAlign: 'right', display: 'inline-block' }}>{d.pct}%</Mono>
                </div>
              );
            })}
          </div>
        </Card>

        <Card style={{ padding: '20px 22px' }}>
          <Label>Sector avg drawdown · most resilient first</Label>
          <div style={{ display: 'grid', gap: 9, marginTop: 16 }}>
            {sectors.map(s => {
              const active = sector === s.name;
              return (
                <div
                  key={s.name}
                  onClick={() => setSector(active ? ALL : s.name)}
                  title="Filter the table to this sector"
                  style={{ display: 'grid', gridTemplateColumns: '96px 1fr 40px 52px', alignItems: 'center', gap: 12, cursor: 'pointer', borderRadius: 6, padding: '2px 4px', margin: '0 -4px', background: active ? T.borderSoft : 'transparent' }}
                >
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: active ? T.navy : T.ink }}>{s.name}</div>
                  <Meter pct={s.avg / maxAvg * 100} color={s.avg < 8 ? T.navy : s.avg < 20 ? T.amber : T.down} height={7} />
                  <Mono size={11.5} color={T.faint} style={{ textAlign: 'right', display: 'inline-block' }}>{s.count}</Mono>
                  <Mono size={12.5} weight={600} color={s.avg < 8 ? T.navy : s.avg < 20 ? T.amber : T.down} style={{ textAlign: 'right', display: 'inline-block' }}>-{s.avg.toFixed(1)}%</Mono>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      <div style={{ marginTop: 16 }}>
        <TableShell>
          <TableHead grid={GRID} cols={COLS} sort={sort} onSort={onSort} />
          {sorted.map(s => {
            const b = ddBucket(s.distATH);
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
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Meter pct={Math.min(100, s.distATH / 60 * 100)} color={b.color} height={6} />
                  <Mono size={12} weight={600} color={b.color} style={{ width: 48, textAlign: 'right', display: 'inline-block' }}>-{s.distATH.toFixed(1)}%</Mono>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em', padding: '3px 9px', borderRadius: 6, background: T.borderSoft, color: b.color }}>{b.label}</span>
                </div>
              </div>
            );
          })}
          {sorted.length === 0 && (
            <div style={{ padding: '28px 22px', textAlign: 'center', fontSize: 13, color: T.faint }}>
              No stocks match {filterNote || 'this filter'}.
            </div>
          )}
          <Footnote>
            {sorted.length} of {D.stocks.length} stocks{filterNote ? ' · ' + filterNote : ''} · {sortLabel(sort, headLabels)} · click a row for fundamentals
          </Footnote>
        </TableShell>
      </div>
    </div>
  );
}
