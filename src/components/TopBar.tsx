import { useEffect, useRef, useState } from 'react';
import ikigaiIcon from '../assets/ikigai-mark.jpeg';
import { T } from '../theme';
import { Label, Mono, inputStyle } from './ui';
import type { Profile } from '../lib/profile';
import { fmtInr } from '../lib/profile';

function marketStatus(now: Date) {
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
  const day = ist.getDay();
  const mins = ist.getHours() * 60 + ist.getMinutes();
  const open = day >= 1 && day <= 5 && mins >= 555 && mins <= 930; // 09:15–15:30 IST
  return { open, label: open ? 'MARKET OPEN' : 'MARKET CLOSED' };
}

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

import type { SessionUser } from '../lib/api';

export function TopBar({ profile, updateProfile, watchCount, sessionUser, dataSource, asOf, onLogout }: {
  profile: Profile;
  updateProfile: (p: Partial<Profile>) => void;
  watchCount: number;
  sessionUser?: SessionUser | null;
  dataSource?: 'live' | 'demo';
  asOf?: string | null;
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
    timeZone: 'Asia/Kolkata', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).replace(',', '') + ' IST';
  const riskPerTrade = profile.capital * profile.riskPct / 100;

  return (
    <div style={{ background: T.card, borderBottom: '1px solid ' + T.border, position: 'sticky', top: 0, zIndex: 40 }}>
      <div style={{ maxWidth: 1280, margin: '0 auto', padding: '10px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
          <img src={ikigaiIcon} alt="Ikigai" style={{ height: 44, width: 'auto', display: 'block', mixBlendMode: 'multiply' }} />
          <div style={{ lineHeight: 1 }}>
            <div style={{ fontFamily: T.serif, fontSize: 21, fontWeight: 600, letterSpacing: '0.01em', color: T.ink }}>Pulse</div>
            <div style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '0.22em', color: T.faint, marginTop: 4 }}>IKIGAI TRADER · SWING TERMINAL</div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <Mono size={12} color={T.muted}>{clock}</Mono>
          {dataSource && (
            <div
              title={dataSource === 'live' ? 'NSE EOD data' + (asOf ? ' · as of ' + asOf : '') : 'Synthetic demo data — backend not connected'}
              style={{ display: 'flex', alignItems: 'center', gap: 6, background: dataSource === 'live' ? T.amberSoft : T.borderSoft, borderRadius: 99, padding: '4px 12px' }}
            >
              <span style={{ width: 6, height: 6, borderRadius: 99, background: dataSource === 'live' ? T.amber : T.faint, display: 'block' }} />
              <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.07em', color: dataSource === 'live' ? T.amber : T.muted }}>
                {dataSource === 'live' ? 'NSE DATA' + (asOf ? ' · ' + asOf.slice(5) : '') : 'DEMO DATA'}
              </span>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: status.open ? T.upSoft : T.borderSoft, borderRadius: 99, padding: '4px 12px' }}>
            <span style={{ width: 6, height: 6, borderRadius: 99, background: status.open ? T.up : T.faint, display: 'block' }} />
            <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.07em', color: status.open ? T.up : T.muted }}>{status.label}</span>
          </div>

          <div ref={menuRef} style={{ position: 'relative' }}>
            <button
              onClick={() => setMenuOpen(v => !v)}
              title="Profile"
              style={{ appearance: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 9, border: '1px solid ' + (menuOpen ? T.faint : T.border), background: T.card, borderRadius: 99, padding: '4px 12px 4px 5px' }}
            >
              <span style={{ width: 26, height: 26, borderRadius: 99, background: T.navy, color: '#F4F2EC', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: T.serif, fontSize: 13, fontWeight: 600 }}>
                {(sessionUser?.name ?? profile.name).charAt(0).toUpperCase()}
              </span>
              <span style={{ fontFamily: T.sans, fontSize: 12.5, fontWeight: 600, color: T.ink }}>{sessionUser?.name ?? profile.name}</span>
            </button>

            {menuOpen && (
              <div style={{ position: 'absolute', right: 0, top: 'calc(100% + 8px)', width: 320, background: T.card, border: '1px solid ' + T.border, borderRadius: 14, boxShadow: '0 12px 32px rgba(35,43,56,0.12)', padding: 20, animation: 'fade-in 120ms ease' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <img src={ikigaiIcon} alt="" style={{ height: 44, mixBlendMode: 'multiply' }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: T.serif, fontSize: 16, fontWeight: 600, color: T.ink }}>{sessionUser?.name ?? profile.name}</div>
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
        </div>
      </div>
    </div>
  );
}
