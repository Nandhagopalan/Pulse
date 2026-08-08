import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchPrefs, savePrefs, type SessionUser } from './api';

/** Trading preferences — the only part of the profile a user edits. */
export interface Prefs {
  capital: number;   // ₹
  riskPct: number;   // % of capital risked per trade
  maxPos: number;    // max open positions
}

/** Prefs plus the read-only identity that comes from the signed-in account. */
export interface Profile extends Prefs {
  name: string;
  handle: string;
  email: string;
  style: string;
}

export const DEFAULT_PREFS: Prefs = {
  capital: 1000000,
  riskPct: 1,
  maxPos: 6,
};

const STYLE = 'Momentum swing · NSE';

// Pre-account local copy. Migrated onto the account on first authenticated load,
// then cleared — the server is the only home for preferences now.
const LEGACY_PREFS_KEYS = ['pulse-profile', 'pulse-prefs-v2'];

function readLegacyPrefs(): Partial<Prefs> | null {
  for (const key of LEGACY_PREFS_KEYS) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const parsed = JSON.parse(raw) as Partial<Prefs>;
      if (parsed && typeof parsed === 'object') return parsed;
    } catch { /* ignore corrupt storage */ }
  }
  return null;
}

function clearLegacyPrefs(): void {
  for (const key of LEGACY_PREFS_KEYS) {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
  }
}

function handleFor(user: SessionUser | null): string {
  const local = user?.email?.split('@')[0] ?? '';
  const slug = local.replace(/[^a-z0-9]/gi, '').toLowerCase();
  return slug ? '@' + slug : '@trader';
}

/**
 * Identity comes from the SSO session; preferences live on the account, so they
 * follow the user across devices. Writes are debounced because the capital and
 * risk fields commit on every keystroke-ish edit.
 */
export function useProfile(user: SessionUser | null) {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      try {
        const { prefs: stored } = await fetchPrefs();
        if (cancelled) return;
        if (stored) {
          setPrefs({ ...DEFAULT_PREFS, ...stored });
          clearLegacyPrefs();
          return;
        }
        // Nothing on the account yet — adopt the browser-local copy once.
        const legacy = readLegacyPrefs();
        const seeded = { ...DEFAULT_PREFS, ...(legacy ?? {}) };
        setPrefs(seeded);
        if (legacy) {
          await savePrefs(seeded);
          clearLegacyPrefs();
        }
      } catch { /* keep defaults — the terminal still works */ }
    })();
    return () => { cancelled = true; };
  }, [user]);

  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current); }, []);

  const update = (patch: Partial<Prefs>) => {
    setPrefs(prev => {
      const next = { ...prev, ...patch };
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        savePrefs(next).catch(() => { /* next edit retries */ });
      }, 600);
      return next;
    });
  };

  const profile = useMemo<Profile>(() => ({
    ...prefs,
    name: user?.name ?? 'Trader',
    handle: handleFor(user),
    email: user?.email ?? '',
    style: STYLE,
  }), [prefs, user]);

  return { profile, update };
}

export function fmtInr(v: number) {
  if (v >= 10000000) return '₹' + (v / 10000000).toFixed(2).replace(/\.00$/, '') + ' Cr';
  if (v >= 100000) return '₹' + (v / 100000).toFixed(2).replace(/\.00$/, '') + ' L';
  return '₹' + v.toLocaleString('en-IN');
}
