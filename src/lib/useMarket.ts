import { useCallback, useEffect, useRef, useState } from 'react';
import type { MarketData } from './data';
import { buildData } from './data';
import { ApiError, fetchMe, fetchSummary, type BackfillStatus, type SessionUser } from './api';

export type AuthState = 'checking' | 'anon' | 'authed' | 'offline';
export type DataSource = 'live' | 'demo';

export interface MarketState {
  auth: AuthState;
  user: SessionUser | null;
  data: MarketData | null;
  source: DataSource;
  asOf: string | null;
  backfill: BackfillStatus | null;
  enableDemo: () => void;
  refresh: () => void;
}

const REFRESH_MS = 30_000;   // summary refresh cadence (live overlay during market hours)
const BACKFILL_POLL_MS = 8_000;

export function useMarket(): MarketState {
  const [auth, setAuth] = useState<AuthState>('checking');
  const [user, setUser] = useState<SessionUser | null>(null);
  const [data, setData] = useState<MarketData | null>(null);
  const [source, setSource] = useState<DataSource>('live');
  const [asOf, setAsOf] = useState<string | null>(null);
  const [backfill, setBackfill] = useState<BackfillStatus | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const enableDemo = useCallback(() => {
    setData(buildData());
    setSource('demo');
    setAuth(a => (a === 'authed' ? a : 'offline'));
  }, []);

  const loadSummary = useCallback(async () => {
    try {
      const summary = await fetchSummary();
      setData(summary);
      setSource('live');
      setAsOf(summary.date);
      setBackfill(null);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(loadSummary, REFRESH_MS);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        // Data pipeline still bootstrapping — poll until analytics land.
        const body = err.body as { backfill?: BackfillStatus } | null;
        setBackfill(body?.backfill ?? { running: true, done: 0, target: 0, currentDate: null, error: null });
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(loadSummary, BACKFILL_POLL_MS);
      } else if (err instanceof ApiError && err.status === 401) {
        setAuth('anon');
      } else {
        enableDemo(); // backend unreachable
      }
    }
  }, [enableDemo]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (cancelled) return;
        setUser(me.user);
        setAuth('authed');
        void loadSummary();
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) setAuth('anon');
        else enableDemo();
      }
    })();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [loadSummary, enableDemo]);

  return { auth, user, data, source, asOf, backfill, enableDemo, refresh: loadSummary };
}
