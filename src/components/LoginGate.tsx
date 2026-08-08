import ikigaiIcon from '../assets/ikigai-mark.jpeg';
import { T } from '../theme';
import { googleLoginUrl, loginErrorMessage } from '../lib/api';
import type { BackfillStatus } from '../lib/api';
import { Mono } from './ui';

export function LoginGate() {
  const error = loginErrorMessage(window.location.search);
  return (
    <div style={{ minHeight: '100vh', background: T.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 380, background: T.card, border: '1px solid ' + T.border, borderRadius: 18, boxShadow: T.shadow, padding: '40px 36px', textAlign: 'center' }}>
        <img src={ikigaiIcon} alt="Ikigai" style={{ height: 64, mixBlendMode: 'multiply' }} />
        <div style={{ fontFamily: T.serif, fontSize: 28, fontWeight: 600, color: T.ink, marginTop: 14 }}>Pulse</div>
        <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.24em', color: T.faint, marginTop: 6 }}>SWING TERMINAL · NSE</div>

        <div style={{ height: 1, background: T.borderSoft, margin: '28px 0' }} />

        <div style={{ fontSize: 13.5, color: T.muted, lineHeight: 1.6 }}>
          Sign in to load NSE market breadth, sector strength and swing setups.
        </div>

        {error && (
          <div style={{
            marginTop: 16, padding: '10px 12px', borderRadius: 10, textAlign: 'left',
            background: T.cardAlt, border: '1px solid ' + T.border, fontSize: 12.5, color: T.ink, lineHeight: 1.5,
          }}>
            {error}
          </div>
        )}

        <a
          href={googleLoginUrl}
          style={{
            display: 'block', marginTop: 22, padding: '13px 0', borderRadius: 12, textDecoration: 'none',
            background: T.ink, color: '#F4F2EC', fontFamily: T.sans, fontSize: 14.5, fontWeight: 600,
          }}
        >
          Sign in with Google
        </a>

      </div>
    </div>
  );
}

/** Shown when the API cannot be reached — never a synthetic market. */
export function OfflineScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <div style={{ minHeight: '100vh', background: T.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 380, background: T.card, border: '1px solid ' + T.border, borderRadius: 18, boxShadow: T.shadow, padding: '40px 36px', textAlign: 'center' }}>
        <div style={{ fontFamily: T.serif, fontSize: 22, fontWeight: 600, color: T.ink }}>Market data unavailable</div>
        <div style={{ fontSize: 13.5, color: T.muted, marginTop: 10, lineHeight: 1.6 }}>
          Pulse could not reach its API, so there is nothing to show. Every figure here
          is an NSE end-of-day close — none of it is generated locally.
        </div>
        <button
          onClick={onRetry}
          style={{
            appearance: 'none', border: 'none', cursor: 'pointer', width: '100%',
            marginTop: 22, padding: '13px 0', borderRadius: 12,
            background: T.ink, color: '#F4F2EC', fontFamily: T.sans, fontSize: 14.5, fontWeight: 600,
          }}
        >
          Try again
        </button>
      </div>
    </div>
  );
}

export function BootstrapScreen({ backfill }: { backfill: BackfillStatus }) {
  const pct = backfill.target > 0 ? Math.round(backfill.done / backfill.target * 100) : 0;
  return (
    <div style={{ minHeight: '100vh', background: T.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 420, background: T.card, border: '1px solid ' + T.border, borderRadius: 18, boxShadow: T.shadow, padding: '36px 34px' }}>
        <div style={{ fontFamily: T.serif, fontSize: 20, fontWeight: 600, color: T.ink }}>Preparing market history</div>
        <div style={{ fontSize: 13, color: T.muted, marginTop: 10, lineHeight: 1.6 }}>
          {backfill.error
            ? 'Backfill hit an error — check the server logs. ' + backfill.error
            : 'Downloading official NSE end-of-day archives and computing breadth analytics. This runs once on first launch.'}
        </div>
        <div style={{ marginTop: 20, height: 8, borderRadius: 99, background: T.borderSoft, overflow: 'hidden' }}>
          <div style={{ width: pct + '%', height: '100%', background: T.amber, transition: 'width 400ms ease' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
          <Mono size={12} color={T.muted}>{backfill.done} / {backfill.target || '—'} sessions</Mono>
          <Mono size={12} color={T.faint}>{backfill.currentDate ?? ''}</Mono>
        </div>
      </div>
    </div>
  );
}
