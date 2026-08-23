import { T, dirColor } from '../../theme';
import { Card, Label } from '../ui';
import { StockTable } from '../StockTable';
import { FilterBox } from '../FilterBox';
import { filterStocks } from '../../lib/search';
import type { MarketData } from '../../lib/data';
import { cols, type Media } from '../../lib/useMedia';

export function WatchTab({ D, watch, toggle, onOpen, media, onSearch, query, setQuery }: {
  D: MarketData;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
  media: Media;
  onSearch?: () => void;
  query: string;
  setQuery: (q: string) => void;
}) {
  const tracked = D.stocks.filter(s => watch[s.sym]);
  const rows = filterStocks(tracked, query);

  if (tracked.length === 0) {
    return (
      <div style={{ marginTop: 18 }}>
        <div style={{ background: T.card, border: '1px dashed ' + T.faint, borderRadius: T.radius, padding: '48px 22px', textAlign: 'center' }}>
          <div style={{ fontFamily: T.serif, fontSize: 17, fontWeight: 600, color: T.ink }}>Nothing tracked yet</div>
          <div style={{ fontSize: 13.5, color: T.muted, marginTop: 6 }}>Search for a symbol, or star stocks from the Highs or Drawdown tab.</div>
          {onSearch && (
            <button
              onClick={onSearch}
              style={{ appearance: 'none', cursor: 'pointer', border: 0, background: T.ink, color: '#fff', borderRadius: 8, fontFamily: T.sans, fontSize: 13, fontWeight: 600, padding: '9px 16px', marginTop: 16, display: 'inline-flex', alignItems: 'center', gap: 8 }}
            >
              <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
              Search symbols
            </button>
          )}
        </div>
      </div>
    );
  }

  const avg1w = tracked.reduce((a, s) => a + s.chg1w, 0) / tracked.length;
  const avgFromAth = tracked.reduce((a, s) => a + s.distATH, 0) / tracked.length;
  const atHighs = tracked.filter(s => s.isATH || s.is52).length;

  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: cols(media, 3, 2, 2), gap: media.isMobile ? 10 : 14, marginBottom: 16 }}>
        {[
          { label: 'Tracked', value: String(tracked.length), color: T.ink, sub: 'on your watchlist' },
          { label: 'Avg 1W move', value: (avg1w >= 0 ? '+' : '') + avg1w.toFixed(1) + '%', color: dirColor(avg1w), sub: 'across watchlist' },
          { label: 'Avg from ATH', value: '-' + avgFromAth.toFixed(1) + '%', color: avgFromAth < 10 ? T.up : T.amber, sub: atHighs + ' at 52w or all-time highs' },
        ].map(c => (
          <Card key={c.label} style={{ padding: '14px 18px' }}>
            <Label>{c.label}</Label>
            <div style={{ fontFamily: T.serif, fontSize: 24, fontWeight: 600, color: c.color, marginTop: 6 }}>{c.value}</div>
            <div style={{ fontSize: 11.5, color: T.faint, marginTop: 3 }}>{c.sub}</div>
          </Card>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <FilterBox value={query} onChange={setQuery} count={rows.length} total={tracked.length} />
      </div>

      {rows.length === 0 ? (
        <div style={{ background: T.card, border: '1px dashed ' + T.faint, borderRadius: T.radius, padding: '32px 22px', textAlign: 'center', fontSize: 13.5, color: T.muted }}>
          None of your {tracked.length} tracked stocks match &ldquo;{query}&rdquo;.
        </div>
      ) : (
      <StockTable
        stocks={rows}
        watch={watch}
        toggle={toggle}
        onOpen={onOpen}
        footnote="Click a row for price action and trend detail. Sizing lives on the Strategy tab."
      />
      )}
    </div>
  );
}
