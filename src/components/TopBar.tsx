import { useEffect, useRef, useState } from 'react';
import { T } from '../theme';
import { Label, Mono, inputStyle } from './ui';
import type { Prefs, Profile } from '../lib/profile';
import { fmtInr } from '../lib/profile';
import { marketStatus } from '../lib/market';
import type { SessionUser } from '../lib/api';

function ProfileField({ label, value, onCommit, prefix }: {
  label: string; value: number; onCommit: (n: number) => void; prefix?: string;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const n = parseFloat(draft);
    if (Number.isFinite(n) && n > 0) onCommit(n); else setDraft(String(value));
  };
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 600, color: T.muted, marginBottom: 4 }}>{label}</div>
      <div style={{ position: 'relative' }}>
        {prefix && <span style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', fontSize: 13, color: T.faint }}>{prefix}</span>}
        <input
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          style={{ ...inputStyle, fontFamily: T.mono, fontSize: 13, paddingLeft: prefix ? 26 : 12 }}
        />
      </div>
    </div>
  );
}

export function TopBar({ title, profile, updateProfile, watchCount, sessionUser, asOf, onRefresh, onLogout }: {
  title: string;
  profile: Profile;
  updateProfile: (p: Partial<Prefs>) => void;
  watchCount: number;
  sessionUser?: SessionUser | null;
  asOf?: string | null;
  onRefresh?: () => void;
  onLogout?: () => void;
}) {
  const [now, setNow] = useState(() => new Date());
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  const status = marketStatus(now);
  const clock = now.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).replace(',', '') + ' IST';
  const riskPerTrade = profile.capital * profile.riskPct / 100;

  const pill = (bg: string, dot: string, textColor: string, label: string, title?: string) => (
    <div title={title} style={{ display: 'flex', alignItems: 'center', gap: 7, height: 30, padding: '0 12px', borderRadius: 99, background: bg, boxShadow: bg === T.card ? 'inset 0 0 0 1px ' + T.border : 'none' }}>
      <span style={{ width: 7, height: 7, borderRadius: 99, background: dot, display: 'block' }} />
      <span style={{ fontSize: 12, fontWeight: 600, color: textColor }}>{label}</span>
    </div>
  );

  return (
    <header style={{ height: 56, flexShrink: 0, position: 'sticky', top: 0, zIndex: 15, background: 'rgba(247,246,243,0.82)', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', borderBottom: '1px solid ' + T.border, display: 'flex', alignItems: 'center', gap: 16, padding: '0 24px' }}>
      <div style={{ fontSize: 15, fontWeight: 700, letterSpacing: '-0.01em', color: T.ink }}>{title}</div>
      <div style={{ flex: 1 }} />

      <Mono size={11.5} color={T.faint}>{clock}</Mono>

      {pill(T.amberSoft, T.amber, T.amber, 'NSE' + (asOf ? ' · ' + asOf.slice(5) : ''), 'NSE EOD data' + (asOf ? ' · as of ' + asOf : ''))}

      {status.open
        ? pill(T.upSoft, T.up, T.up, status.label)
        : pill(T.card, T.faint, T.text, status.label)}

      {onRefresh && (
        <button
          onClick={onRefresh}
          title="Refresh"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px', borderRadius: 8, border: 0, background: T.card, boxShadow: 'inset 0 0 0 1px ' + T.border, color: T.text, fontFamily: T.sans, fontSize: 12.5, fontWeight: 600, cursor: 'pointer' }}
        >
          <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M23 4v6h-6M1 20v-6h6" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></svg>
          Refresh
        </button>
      )}

      <div ref={menuRef} style={{ position: 'relative' }}>
        <button
          onClick={() => setMenuOpen(v => !v)}
          title="Profile"
          style={{ appearance: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, border: 0, background: T.card, boxShadow: 'inset 0 0 0 1px ' + (menuOpen ? T.faint : T.border), borderRadius: 99, padding: '3px 12px 3px 4px', height: 32 }}
        >
          <span style={{ width: 24, height: 24, borderRadius: 99, background: 'linear-gradient(135deg,#3184CB,#1b3d61)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11.5, fontWeight: 700 }}>
            {(sessionUser?.name ?? profile.name).charAt(0).toUpperCase()}
          </span>
          <span style={{ fontFamily: T.sans, fontSize: 12.5, fontWeight: 600, color: T.ink }}>{sessionUser?.name ?? profile.name}</span>
        </button>

        {menuOpen && (
          <div style={{ position: 'absolute', right: 0, top: 'calc(100% + 8px)', width: 320, background: T.card, border: '1px solid ' + T.border, borderRadius: 14, boxShadow: T.shadowPop, padding: 20, animation: 'fade-in 120ms ease' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: T.ink }}>{sessionUser?.name ?? profile.name}</div>
                <div style={{ fontSize: 12, color: T.muted, marginTop: 2 }}>{profile.handle} · {profile.style}</div>
                <div style={{ fontSize: 11.5, color: T.faint, marginTop: 1 }}>{sessionUser?.email ?? profile.email}</div>
              </div>
              {sessionUser && onLogout && (
                <button
                  onClick={onLogout}
                  style={{ appearance: 'none', cursor: 'pointer', border: '1px solid ' + T.border, background: T.cardAlt, borderRadius: 8, padding: '5px 10px', fontFamily: T.sans, fontSize: 11.5, fontWeight: 600, color: T.muted }}
                >
                  Sign out
                </button>
              )}
            </div>

            <div style={{ height: 1, background: T.borderSoft, margin: '16px 0' }} />

            <Label>Risk settings</Label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
              <ProfileField label="Capital" value={profile.capital} onCommit={n => updateProfile({ capital: n })} prefix="₹" />
              <ProfileField label="Risk / trade %" value={profile.riskPct} onCommit={n => updateProfile({ riskPct: n })} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
              <ProfileField label="Max positions" value={profile.maxPos} onCommit={n => updateProfile({ maxPos: Math.round(n) })} />
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: T.muted, marginBottom: 4 }}>Risk per trade</div>
                <div style={{ padding: '8px 12px', border: '1px solid ' + T.borderSoft, borderRadius: 8, background: T.cardAlt }}>
                  <Mono size={13} color={T.amber} weight={600}>{fmtInr(riskPerTrade)}</Mono>
                </div>
              </div>
            </div>

            <div style={{ height: 1, background: T.borderSoft, margin: '16px 0' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: T.muted }}>
              <span>Watchlist</span>
              <Mono size={12}>{watchCount} stocks</Mono>
            </div>
            <div style={{ fontSize: 11, color: T.faint, marginTop: 10, lineHeight: 1.5 }}>
              Position sizes across the terminal are computed from these settings.
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
