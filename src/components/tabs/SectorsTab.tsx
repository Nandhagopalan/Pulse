import { T, dirColor } from '../../theme';
import { Mono, Meter } from '../ui';
import { TableShell, TableHead, Footnote } from '../StockTable';
import type { MarketData } from '../../lib/data';
import { fmtPct } from '../../lib/format';

const GRID = '1.5fr 0.7fr 0.9fr 1.4fr 0.8fr 0.7fr 1.2fr';

export function SectorsTab({ D }: { D: MarketData }) {
  return (
    <div style={{ marginTop: 18 }}>
      <TableShell>
        <TableHead grid={GRID} cols={['Sector', ['Stocks', 'right'], ['Adv / Dec', 'right'], '% above 50 DMA', ['New highs', 'right'], ['1W %', 'right'], 'Strength']} />
        {D.sectors.map(s => (
          <div key={s.name} style={{ display: 'grid', gridTemplateColumns: GRID, gap: 14, alignItems: 'center', padding: '12px 22px', borderBottom: '1px solid ' + T.borderSoft, fontSize: 13.5 }}>
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
        ))}
        <Footnote>Strength = composite of % above 50 DMA, weekly momentum and rate of new highs. Ranked strongest first.</Footnote>
      </TableShell>
    </div>
  );
}
