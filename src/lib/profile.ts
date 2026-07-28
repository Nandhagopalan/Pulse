import { useEffect, useState } from 'react';

export interface Profile {
  name: string;
  handle: string;
  email: string;
  style: string;
  capital: number;   // ₹
  riskPct: number;   // % of capital risked per trade
  maxPos: number;    // max open positions
}

export const DEFAULT_PROFILE: Profile = {
  name: 'Ikigai Trader',
  handle: '@ikigaitrader',
  email: 'nandhu.elan@gmail.com',
  style: 'Momentum swing · NSE',
  capital: 1000000,
  riskPct: 1,
  maxPos: 6,
};

export function useProfile() {
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  useEffect(() => {
    try {
      const p = localStorage.getItem('pulse-profile');
      if (p) setProfile({ ...DEFAULT_PROFILE, ...JSON.parse(p) });
    } catch { /* ignore corrupt storage */ }
  }, []);
  const update = (patch: Partial<Profile>) => {
    setProfile(prev => {
      const next = { ...prev, ...patch };
      try { localStorage.setItem('pulse-profile', JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  };
  return { profile, update };
}

export function fmtInr(v: number) {
  if (v >= 10000000) return '₹' + (v / 10000000).toFixed(2).replace(/\.00$/, '') + ' Cr';
  if (v >= 100000) return '₹' + (v / 100000).toFixed(2).replace(/\.00$/, '') + ' L';
  return '₹' + v.toLocaleString('en-IN');
}
