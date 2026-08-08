import { T } from '../../theme';
import { StockTable } from '../StockTable';
import type { MarketData, Stock } from '../../lib/data';

export type HighMode = 'w52' | 'ath' | 'wk';

export function HighsTab({ D, highMode, setHighMode, sectorFilter, setSectorFilter, watch, toggle, onOpen }: {
  D: MarketData;
  highMode: HighMode;
  setHighMode: (m: HighMode) => void;
  sectorFilter: string;
  setSectorFilter: (s: string) => void;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
}) {
  const pools: Record<HighMode, Stock[]> = {
    ath: D.stocks.filter(s => s.isATH),
    w52: D.stocks.filter(s => s.is52),
    wk: D.stocks.filter(s => s.wkBreak),
  };
  const modes: { id: HighMode; label: string }[] = [
    { id: 'w52', label: '52-week highs' },
    { id: 'ath', label: 'All-time highs' },
    { id: 'wk', label: 'Weekly breakouts' },
  ];
  const pool = pools[highMode];
  // Counts reflect the active high-mode, so the dropdown shows where the
  // breakouts actually are before you commit to a sector.
  const perSector: Record<string, number> = {};
  pool.forEach(s => { perSector[s.sector] = (perSector[s.sector] || 0) + 1; });
  const sectorOptions = ['All sectors', ...D.sectors.map(s => s.name)];
  const rows = pool.filter(s => sectorFilter === 'All sectors' || s.sector === sectorFilter);

  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          {modes.map(m => {
            const active = highMode === m.id;
            return (
              <button key={m.id} onClick={() => setHighMode(m.id)} style={{ appearance: 'none', cursor: 'pointer', fontFamily: T.sans, fontSize: 13, fontWeight: 600, padding: '7px 14px', borderRadius: 99, border: '1px solid ' + (active ? T.ink : T.border), background: active ? T.ink : T.card, color: active ? T.card : T.text }}>
                {m.label} · {pools[m.id].length}
              </button>
            );
          })}
        </div>
        <select value={sectorFilter} onChange={e => setSectorFilter(e.target.value)} style={{ fontFamily: T.sans, fontSize: 13, fontWeight: 500, padding: '8px 12px', borderRadius: 8, border: '1px solid ' + T.border, background: T.card, color: T.text }}>
          {sectorOptions.map(o => (
            <option key={o} value={o}>{o === 'All sectors' ? o + ' · ' + pool.length : o + ' · ' + (perSector[o] || 0)}</option>
          ))}
        </select>
      </div>

      <div style={{ marginTop: 16 }}>
        <StockTable
          stocks={rows}
          watch={watch}
          toggle={toggle}
          onOpen={onOpen}
          initialSort={{ key: 'chg1w', dir: 'desc' }}
          footnote={rows.length + ' stocks · click any column header to sort · click a row for detail, star it to watch'}
        />
      </div>
    </div>
  );
}
