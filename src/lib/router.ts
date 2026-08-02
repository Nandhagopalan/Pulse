import { useCallback, useEffect, useMemo, useState } from 'react';

// Hash routing keeps Pulse deployable as plain static files — no server rewrite
// rules, and the Vite dev proxy for /api and /auth stays untouched.

export interface Route {
  /** Path segments after the leading '#/', already decoded. e.g. ['highs', 'w52'] */
  segments: string[];
  query: URLSearchParams;
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const qi = raw.indexOf('?');
  const path = qi === -1 ? raw : raw.slice(0, qi);
  const query = new URLSearchParams(qi === -1 ? '' : raw.slice(qi + 1));
  const segments = path.split('/').filter(Boolean).map(s => {
    try { return decodeURIComponent(s); } catch { return s; }
  });
  return { segments, query };
}

export function buildHash(segments: string[], query?: Record<string, string | null | undefined>): string {
  const path = segments.filter(Boolean).map(encodeURIComponent).join('/');
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query || {})) {
    if (v != null && v !== '') params.set(k, v);
  }
  const qs = params.toString();
  return '#/' + path + (qs ? '?' + qs : '');
}

export function useRoute() {
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const route = useMemo(() => parse(hash), [hash]);

  // `replace` avoids stacking history entries for state the user did not
  // explicitly navigate to (default redirects, drawer close, sync-on-mount).
  const navigate = useCallback((target: string, opts?: { replace?: boolean }) => {
    const next = target.startsWith('#') ? target : '#' + target;
    if (next === window.location.hash) return;
    if (opts?.replace) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search + next);
      setHash(next);
    } else {
      window.location.hash = next;
    }
  }, []);

  return { route, navigate };
}

/**
 * Reads one query param and writes it back into the URL, so a filter behaves
 * like state but survives reload and can be shared as a link.
 */
export function useQueryParam(
  route: Route,
  navigate: (target: string, opts?: { replace?: boolean }) => void,
  key: string,
  fallback: string,
): [string, (next: string) => void] {
  const value = route.query.get(key) ?? fallback;
  const set = useCallback((next: string) => {
    const params = new URLSearchParams(route.query);
    if (!next || next === fallback) params.delete(key); else params.set(key, next);
    const path = route.segments.map(encodeURIComponent).join('/');
    const qs = params.toString();
    navigate('#/' + path + (qs ? '?' + qs : ''), { replace: true });
  }, [route, navigate, key, fallback]);
  return [value, set];
}
