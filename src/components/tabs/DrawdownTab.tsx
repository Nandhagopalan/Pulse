import { T } from '../../theme';
import { Card, Label, Mono, Meter } from '../ui';
import { TableShell, TableHead, Footnote } from '../StockTable';
import type { MarketData } from '../../lib/data';
import { fmtPrice } from '../../lib/format';

const GRID = '34px 1.1fr 1fr 0.9fr 1.4fr 1.2fr';

function ddBucket(d: number) {
  if (d < 3) return { key: 'high', label: 'AT / NEAR HIGH', color: T.up };
  if (d < 10) return { key: 'dip', label: 'MILD DIP', color: T.navy };
  if (d < 20) return { key: 'corr', label: 'CORRECTION', color: T.amber };
  if (d < 40) return { key: 'bear', label: 'BEAR -20 TO -40', color: T.down };
  return { key: 'deep', label: 'DEEP >40 · BASING', color: T.ink };
}

export function DrawdownTab({ D, watch, toggle, onOpen }: {
  D: MarketData;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
}) {
  const rows = [...D.stocks].sort((a, b) => b.distATH - a.distATH);

  const buckets: [string, string, string][] = [
    ['high', 'At / near high', T.up], ['dip', 'Mild dip <10%', T.navy],
    ['corr', 'Correction 10-20%', T.amber], ['bear', 'Bear 20-40%', T.down], ['deep', 'Deep >40%', T.ink],
  ];
  const counts: Record<string, number> = { high: 0, dip: 0, corr: 0, bear: 0, deep: 0 };
  D.stocks.forEach(s => { counts[ddBucket(s.distATH).key]++; });
  const total = D.stocks.length;
  const dist = buckets.map(([key, label, color]) => ({ label, color, count: counts[key], pct: Math.round(counts[key] / total * 100), width: (counts[key] / total * 100) + '%' }));

  const bySector: Record<string, number[]> = {};
  D.stocks.forEach(s => { (bySector[s.sector] = bySector[s.sector] || []).push(s.distATH); });
  const sectors = Object.keys(bySector).map(name => {
    const arr = bySector[name]; const avg = arr.reduce((a, b) => a + b, 0) / arr.length;
    return { name, avg, count: arr.length };
  }).sort((a, b) => a.avg - b.avg);
  const maxAvg = Math.max(...sectors.map(s => s.avg), 1);

  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 16 }}>
        <Card style={{ padding: '20px 22px' }}>
          <Label>How far off all-time highs</Label>
          <div style={{ display: 'flex', height: 10, borderRadius: 99, overflow: 'hidden', marginTop: 16, background: T.borderSoft }}>
            {dist.map(d => <div key={d.label} title={d.label} style={{ width: d.width, background: d.color, opacity: 0.85 }} />)}
          </div>
          <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
            {dist.map(d => (
              <div key={d.label} style={{ display: 'grid', gridTemplateColumns: '12px 1fr auto auto', alignItems: 'center', gap: 10 }}>
                <div style={{ width: 10, height: 10, borderRadius: 3, background: d.color, opacity: 0.85 }} />
                <div style={{ fontSize: 13, color: T.text }}>{d.label}</div>
                <Mono size={13} weight={600}>{d.count}</Mono>
                <Mono size={12} color={T.faint} style={{ width: 34, textAlign: 'right', display: 'inline-block' }}>{d.pct}%</Mono>
              </div>
            ))}
          </div>
        </Card>
        <Card style={{ padding: '20px 22px' }}>
          <Label>Sector avg drawdown · most resilient first</Label>
          <div style={{ display: 'grid', gap: 9, marginTop: 16 }}>
            {sectors.map(s => (
              <div key={s.name} style={{ display: 'grid', gridTemplateColumns: '96px 1fr 52px', alignItems: 'center', gap: 12 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: T.ink }}>{s.name}</div>
                <Meter pct={s.avg / maxAvg * 100} color={s.avg < 8 ? T.navy : s.avg < 20 ? T.amber : T.down} height={7} />
                <Mono size={12.5} weight={600} color={s.avg < 8 ? T.navy : s.avg < 20 ? T.amber : T.down} style={{ textAlign: 'right', display: 'inline-block' }}>-{s.avg.toFixed(1)}%</Mono>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div style={{ marginTop: 16 }}>
        <TableShell>
          <TableHead grid={GRID} cols={['', 'Symbol', 'Sector', ['Price', 'right'], 'Fall from ATH', ['Status', 'right']]} />
          {rows.map(s => {
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
          <Footnote>{rows.length + ' stocks · sorted by depth of fall from all-time high · click a row for fundamentals'}</Footnote>
        </TableShell>
      </div>
    </div>
  );
}
