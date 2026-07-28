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
  const sectorOptions = ['All sectors', ...D.sectors.map(s => s.name)];
  const rows = pools[highMode]
    .filter(s => sectorFilter === 'All sectors' || s.sector === sectorFilter)
    .sort((a, b) => b.chg1w - a.chg1w);

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
          {sectorOptions.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>

      <div style={{ marginTop: 16 }}>
        <StockTable
          stocks={rows}
          watch={watch}
          toggle={toggle}
          onOpen={onOpen}
          footnote={rows.length + ' stocks · sorted by 1-week momentum · click a row for fundamentals, star it to watch'}
        />
      </div>
    </div>
  );
}
