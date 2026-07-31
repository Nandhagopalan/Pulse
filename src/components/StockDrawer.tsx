import { useEffect, useMemo } from 'react';
import { T, dirColor } from '../theme';
import { Label, Mono, Meter, Tag } from './ui';
import { stockTag } from './StockTable';
import type { Stock } from '../lib/data';
import { fundamentals, fmtCr } from '../lib/fundamentals';
import { ohlc, candleChart } from '../lib/candles';
import { fmtPct, fmtPrice } from '../lib/format';
import type { Profile } from '../lib/profile';
import { fmtInr } from '../lib/profile';

function Row({ label, value, color, hint }: { label: string; value: string; color?: string; hint?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '7px 0', borderBottom: '1px solid ' + T.borderSoft }}>
      <span style={{ fontSize: 12.5, color: T.muted }}>{label}{hint && <span style={{ color: T.faint, fontSize: 11 }}> · {hint}</span>}</span>
      <Mono size={12.5} color={color || T.ink} weight={600}>{value}</Mono>
    </div>
  );
}

export function StockDrawer({ stock, watch, toggle, profile, onClose }: {
  stock: Stock;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  profile: Profile;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const f = fundamentals(stock.sym, stock.sector, stock.price);
  const candles = useMemo(() => ohlc(stock.sym, stock.price, 2.2, 60), [stock.sym, stock.price]);
  const chart = useMemo(() => candleChart(candles, 380, 150, 6), [candles]);

  // Position plan: stop under the lowest low of the last 10 sessions
  const stop = Math.min(...candles.slice(-10).map(c => c.l)) * 0.995;
  const perShareRisk = Math.max(0.01, stock.price - stop);
  const riskAmt = profile.capital * profile.riskPct / 100;
  const qty = Math.max(0, Math.floor(riskAmt / perShareRisk));
  const posValue = qty * stock.price;
  const posPct = posValue / profile.capital * 100;
  const stopPct = (stop / stock.price - 1) * 100;

  const starred = !!watch[stock.sym];
  const qualityLabel = f.quality >= 65 ? 'STRONG' : f.quality >= 40 ? 'DECENT' : 'WEAK';
  const qualityColor = f.quality >= 65 ? T.up : f.quality >= 40 ? T.amber : T.down;

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(35,43,56,0.28)', zIndex: 50, animation: 'fade-in 140ms ease' }} />
      <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 440, maxWidth: '92vw', background: T.card, boxShadow: '-12px 0 40px rgba(15,23,42,0.10)', zIndex: 51, overflowY: 'auto', padding: '22px 26px 32px', animation: 'drawer-in 180ms ease' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontFamily: T.serif, fontSize: 24, fontWeight: 600, color: T.ink }}>{stock.sym}</span>
              {stockTag(stock)}
            </div>
            <div style={{ fontSize: 12.5, color: T.muted, marginTop: 3 }}>{stock.sector} · NSE</div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => toggle(stock.sym)} title={starred ? 'Remove from watchlist' : 'Add to watchlist'} style={{ appearance: 'none', border: '1px solid ' + T.border, background: starred ? T.amberSoft : T.card, cursor: 'pointer', fontSize: 15, color: starred ? T.amber : T.faint, borderRadius: 8, padding: '4px 10px', lineHeight: 1.3 }}>{starred ? '★' : '☆'}</button>
            <button onClick={onClose} title="Close" style={{ appearance: 'none', border: '1px solid ' + T.border, background: T.card, cursor: 'pointer', fontSize: 15, color: T.muted, borderRadius: 8, padding: '4px 10px', lineHeight: 1.3 }}>✕</button>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginTop: 14 }}>
          <Mono size={24} weight={600}>{fmtPrice(stock.price)}</Mono>
          <Mono size={13} color={dirColor(stock.chg1d)} weight={600}>{fmtPct(stock.chg1d)} 1D</Mono>
          <Mono size={13} color={dirColor(stock.chg1w)} weight={600}>{fmtPct(stock.chg1w)} 1W</Mono>
        </div>
        <div style={{ fontSize: 12, color: T.faint, marginTop: 4 }}>
          {stock.distATH < 0.05 ? 'Trading at its all-time high' : stock.distATH.toFixed(1) + '% below all-time high'}
        </div>

        <svg viewBox="0 0 380 150" preserveAspectRatio="none" style={{ width: '100%', height: 150, marginTop: 16, display: 'block', overflow: 'visible' }}>
          <path d="M0 38 H380 M0 75 H380 M0 112 H380" stroke={T.borderSoft} strokeWidth={1} />
          <path d={chart.ema50} fill="none" stroke={T.navy} strokeWidth={1.3} opacity={0.75} />
          <path d={chart.ema10} fill="none" stroke={T.amber} strokeWidth={1.3} opacity={0.85} />
          <path d={chart.uw} fill={T.up} />
          <path d={chart.dw} fill={T.down} />
          <path d={chart.ub} fill={T.up} />
          <path d={chart.db} fill={T.down} />
        </svg>
        <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 11, color: T.muted }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 12, height: 2, background: T.amber }} />10 EMA {chart.ema10Last}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 12, height: 2, background: T.navy }} />50 EMA {chart.ema50Last}</span>
          <span style={{ color: T.faint }}>60 sessions · sample</span>
        </div>

        <div style={{ marginTop: 22 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Label>Fundamentals</Label>
            <Tag color={qualityColor} bg={T.borderSoft}>{qualityLabel} · {f.quality}</Tag>
          </div>
          <div style={{ marginTop: 6 }}>
            <Row label="Market cap" value={'₹' + fmtCr(f.mcap)} />
            <Row label="P/E" value={f.pe.toFixed(1) + 'x'} />
            <Row label="EPS" value={'₹' + f.eps.toFixed(1)} />
            <Row label="ROE" value={f.roe.toFixed(1) + '%'} color={f.roe >= 15 ? T.up : T.ink} />
            <Row label="ROCE" value={f.roce.toFixed(1) + '%'} color={f.roce >= 15 ? T.up : T.ink} />
            <Row label="OPM" value={f.opm.toFixed(1) + '%'} />
            <Row label="Sales growth" hint="3Y CAGR" value={fmtPct(f.salesG)} color={dirColor(f.salesG)} />
            <Row label="Profit growth" hint="3Y CAGR" value={fmtPct(f.profitG)} color={dirColor(f.profitG)} />
            <Row label="Debt / equity" value={f.de.toFixed(2)} color={f.de > 1.5 && stock.sector !== 'Banks' && stock.sector !== 'Fin Services' ? T.down : T.ink} />
            <Row label="Promoter holding" value={f.promoter.toFixed(1) + '%'} />
            <Row label="Promoter change" hint="QoQ" value={fmtPct(f.promoterChg)} color={dirColor(f.promoterChg)} />
            <Row label="FII / DII holding" value={f.fii.toFixed(1) + '% / ' + f.dii.toFixed(1) + '%'} />
            <Row label="Dividend yield" value={f.divYield.toFixed(2) + '%'} />
          </div>
        </div>

        <div style={{ marginTop: 22, background: T.cardAlt, border: '1px solid ' + T.borderSoft, borderRadius: 12, padding: '16px 18px' }}>
          <Label>Position plan · {profile.riskPct}% risk</Label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 18px', marginTop: 12 }}>
            <div>
              <div style={{ fontSize: 11, color: T.muted }}>Entry</div>
              <Mono size={14} weight={600}>{fmtPrice(stock.price)}</Mono>
            </div>
            <div>
              <div style={{ fontSize: 11, color: T.muted }}>Stop · 10-day low</div>
              <Mono size={14} weight={600} color={T.down}>{fmtPrice(stop)} <span style={{ fontSize: 11 }}>({stopPct.toFixed(1)}%)</span></Mono>
            </div>
            <div>
              <div style={{ fontSize: 11, color: T.muted }}>Quantity</div>
              <Mono size={14} weight={600}>{qty.toLocaleString('en-IN')}</Mono>
            </div>
            <div>
              <div style={{ fontSize: 11, color: T.muted }}>Position value</div>
              <Mono size={14} weight={600} color={T.amber}>{fmtInr(posValue)}</Mono>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <Meter pct={posPct} color={posPct > 40 ? T.down : T.navy} />
            <Mono size={11.5} color={T.muted}>{posPct.toFixed(0)}% of capital</Mono>
          </div>
          <div style={{ fontSize: 11, color: T.faint, marginTop: 10, lineHeight: 1.5 }}>
            Risking {fmtInr(profile.capital * profile.riskPct / 100)} if the stop is hit. Adjust capital and risk in your profile.
          </div>
        </div>
      </div>
    </>
  );
}
