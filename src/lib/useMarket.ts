import { useCallback, useEffect, useRef, useState } from 'react';
import type { MarketData } from './data';
import { ApiError, fetchMe, fetchSummary, type BackfillStatus, type SessionUser } from './api';

/**
 * `offline` means the backend could not be reached. It used to mean "show the
 * synthetic demo market instead", which is a worse answer than saying so: a
 * trading terminal that invents a session when it cannot reach its data is
 * lying at exactly the moment being right matters.
 */
export type AuthState = 'checking' | 'anon' | 'authed' | 'offline';

export interface MarketState {
  auth: AuthState;
  user: SessionUser | null;
  data: MarketData | null;
  asOf: string | null;
  backfill: BackfillStatus | null;
  refresh: () => void;
}

const REFRESH_MS = 30_000;   // summary refresh cadence
const BACKFILL_POLL_MS = 8_000;

export function useMarket(): MarketState {
  const [auth, setAuth] = useState<AuthState>('checking');
  const [user, setUser] = useState<SessionUser | null>(null);
  const [data, setData] = useState<MarketData | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [backfill, setBackfill] = useState<BackfillStatus | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      const summary = await fetchSummary();
      setData(summary);
      setAsOf(summary.date);
      setBackfill(null);
      setAuth('authed');
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(loadSummary, REFRESH_MS);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        // No session published yet — poll until the pipeline lands one.
        const body = err.body as { backfill?: BackfillStatus } | null;
        setBackfill(body?.backfill ?? { running: true, done: 0, target: 0, currentDate: null, error: null });
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(loadSummary, BACKFILL_POLL_MS);
      } else if (err instanceof ApiError && err.status === 401) {
        setAuth('anon');
      } else {
        setAuth('offline');
      }
    }
  }, []);

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
        else setAuth('offline');
      }
    })();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [loadSummary]);

  return { auth, user, data, asOf, backfill, refresh: loadSummary };
}
