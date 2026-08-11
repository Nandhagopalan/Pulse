import { T, dirColor } from '../../theme';
import { Card, Label } from '../ui';
import { StockTable } from '../StockTable';
import type { MarketData } from '../../lib/data';
import type { Profile } from '../../lib/profile';
import { fmtInr } from '../../lib/profile';
import { cols, type Media } from '../../lib/useMedia';

export function WatchTab({ D, watch, toggle, onOpen, profile, media }: {
  D: MarketData;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpen: (sym: string) => void;
  profile: Profile;
  media: Media;
}) {
  const rows = D.stocks.filter(s => watch[s.sym]);

  if (rows.length === 0) {
    return (
      <div style={{ marginTop: 18 }}>
        <div style={{ background: T.card, border: '1px dashed ' + T.faint, borderRadius: T.radius, padding: '48px 22px', textAlign: 'center' }}>
          <div style={{ fontFamily: T.serif, fontSize: 17, fontWeight: 600, color: T.ink }}>Nothing tracked yet</div>
          <div style={{ fontSize: 13.5, color: T.muted, marginTop: 6 }}>Star stocks from the Highs or Drawdown tab to build your swing watchlist.</div>
        </div>
      </div>
    );
  }

  const avg1w = rows.reduce((a, s) => a + s.chg1w, 0) / rows.length;
  const avgFromAth = rows.reduce((a, s) => a + s.distATH, 0) / rows.length;
  const atHighs = rows.filter(s => s.isATH || s.is52).length;
  const slots = profile.maxPos - rows.length;

  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: cols(media, 4, 2, 2), gap: media.isMobile ? 10 : 14, marginBottom: 16 }}>
        {[
          { label: 'Tracked', value: String(rows.length), color: T.ink, sub: slots >= 0 ? slots + ' slots free of ' + profile.maxPos : Math.abs(slots) + ' over your ' + profile.maxPos + ' max' },
          { label: 'Avg 1W move', value: (avg1w >= 0 ? '+' : '') + avg1w.toFixed(1) + '%', color: dirColor(avg1w), sub: 'across watchlist' },
          { label: 'Avg from ATH', value: '-' + avgFromAth.toFixed(1) + '%', color: avgFromAth < 10 ? T.up : T.amber, sub: atHighs + ' at 52w or all-time highs' },
          { label: 'Risk / trade', value: fmtInr(profile.capital * profile.riskPct / 100), color: T.amber, sub: profile.riskPct + '% of capital' },
        ].map(c => (
          <Card key={c.label} style={{ padding: '14px 18px' }}>
            <Label>{c.label}</Label>
            <div style={{ fontFamily: T.serif, fontSize: 24, fontWeight: 600, color: c.color, marginTop: 6 }}>{c.value}</div>
            <div style={{ fontSize: 11.5, color: T.faint, marginTop: 3 }}>{c.sub}</div>
          </Card>
        ))}
      </div>

      <StockTable
        stocks={rows}
        watch={watch}
        toggle={toggle}
        onOpen={onOpen}
        footnote="Click a row for a position plan sized to your profile."
      />
    </div>
  );
}
