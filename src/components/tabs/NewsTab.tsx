import { useEffect, useMemo, useState } from 'react';
import { T } from '../../theme';
import { Card } from '../ui';
import { fetchNews, type NewsArticle } from '../../lib/api';
import type { MarketData } from '../../lib/data';
import { useQueryParam } from '../../lib/router';
import type { Route } from '../../lib/router';

type Navigate = (target: string, opts?: { replace?: boolean }) => void;

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  const days = Math.round(hrs / 24);
  if (days < 30) return days + 'd ago';
  return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
}

function sentimentMeta(s: number | null): { color: string; label: string } {
  if (s == null) return { color: T.faint, label: 'Neutral' };
  if (s > 0.15) return { color: T.up, label: 'Positive' };
  if (s < -0.15) return { color: T.down, label: 'Negative' };
  return { color: T.faint, label: 'Neutral' };
}

const REFRESH_MS = 5 * 60_000; // client re-pull; server refreshes source every 30 min

export function NewsTab({ D, route, navigate, watch }: {
  D: MarketData;
  route: Route;
  navigate: Navigate;
  watch: Record<string, true>;
}) {
  const watchSyms = useMemo(() => Object.keys(watch), [watch]);
  const [state, setState] = useState<{ loading: boolean; enabled: boolean; articles: NewsArticle[]; error: boolean }>(
    { loading: true, enabled: true, articles: [], error: false },
  );
  const [filter, setFilter] = useQueryParam(route, navigate, 'sym', 'all');

  const symKey = watchSyms.join(',');
  useEffect(() => {
    if (watchSyms.length === 0) { setState({ loading: false, enabled: true, articles: [], error: false }); return; }
    let cancelled = false;
    const load = () => {
      // No symbols passed: the server reads them off the account's watchlist.
      fetchNews()
        .then(r => { if (!cancelled) setState({ loading: false, enabled: r.enabled, articles: r.articles, error: false }); })
        .catch(() => { if (!cancelled) setState(s => ({ ...s, loading: false, error: true })); });
    };
    setState(s => ({ ...s, loading: true }));
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [symKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const sectorOf = useMemo(() => {
    const m: Record<string, string> = {};
    for (const s of D.stocks) m[s.sym] = s.sector;
    return m;
  }, [D.stocks]);

  const shown = filter === 'all' ? state.articles : state.articles.filter(a => a.symbol === filter);

  // ── Empty / disabled states ──────────────────────────────
  const shell = (children: React.ReactNode) => <div style={{ marginTop: 18 }}>{children}</div>;

  if (watchSyms.length === 0) {
    return shell(
      <div style={{ background: T.card, border: '1px dashed ' + T.faint, borderRadius: T.radius, padding: '48px 22px', textAlign: 'center' }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: T.ink }}>No stocks tracked yet</div>
        <div style={{ fontSize: 13.5, color: T.muted, marginTop: 6 }}>Star stocks from Highs or Drawdown to follow their news here.</div>
      </div>,
    );
  }
  if (!state.enabled) {
    return shell(
      <div style={{ background: T.card, borderRadius: T.radius, boxShadow: T.shadow + ', inset 0 0 0 1px ' + T.borderSoft, padding: '40px 22px', textAlign: 'center' }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: T.ink }}>News source not configured</div>
        <div style={{ fontSize: 13.5, color: T.muted, marginTop: 6, lineHeight: 1.5 }}>
          Set <code style={{ fontFamily: T.mono, fontSize: 12.5, background: T.cardAlt, padding: '2px 6px', borderRadius: 6 }}>MARKETAUX_API_KEY</code> in the server env to enable watchlist news.
        </div>
      </div>,
    );
  }

  return shell(
    <>
      {/* Filter chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
        {[['all', 'All · ' + state.articles.length] as const,
          ...watchSyms.map(s => [s, s] as const)].map(([id, label]) => {
          const active = filter === id;
          const count = id === 'all' ? null : state.articles.filter(a => a.symbol === id).length;
          return (
            <button
              key={id}
              onClick={() => setFilter(id)}
              style={{
                appearance: 'none', cursor: 'pointer', fontFamily: T.sans, fontSize: 13, fontWeight: 600,
                padding: '7px 14px', borderRadius: 99, border: 'none',
                background: active ? T.ink : T.card, color: active ? '#fff' : T.text,
                boxShadow: active ? 'none' : 'inset 0 0 0 1px ' + T.border,
              }}
            >
              {label}{count != null && <span style={{ opacity: 0.6 }}> · {count}</span>}
            </button>
          );
        })}
      </div>

      {state.loading && state.articles.length === 0 && (
        <Card style={{ padding: '40px 22px', textAlign: 'center', color: T.muted, fontSize: 13.5 }}>Loading news…</Card>
      )}

      {!state.loading && shown.length === 0 && (
        <Card style={{ padding: '40px 22px', textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: T.ink }}>No news yet</div>
          <div style={{ fontSize: 13, color: T.muted, marginTop: 6 }}>
            {state.error ? 'Could not reach the news service — will retry.' : 'Nothing recent for these stocks. Refreshes every 30 minutes.'}
          </div>
        </Card>
      )}

      {shown.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {shown.map(a => {
            const sent = sentimentMeta(a.sentiment);
            return (
              <a
                key={a.id + a.symbol}
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}
              >
                <Card style={{ padding: '16px 18px', transition: 'box-shadow .12s', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: T.mono, fontSize: 11.5, fontWeight: 700, color: T.navy, background: T.navySoft, borderRadius: 99, padding: '3px 9px' }}>{a.symbol}</span>
                    <span style={{ fontSize: 11.5, color: T.faint }}>{sectorOf[a.symbol] ?? ''}</span>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 600, color: sent.color }}>
                      <span style={{ width: 6, height: 6, borderRadius: 99, background: sent.color, display: 'block' }} />{sent.label}
                    </span>
                    <span style={{ flex: 1 }} />
                    <span style={{ fontSize: 11.5, color: T.faint, whiteSpace: 'nowrap' }}>{relTime(a.publishedAt)}</span>
                  </div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: T.ink, lineHeight: 1.4 }}>{a.title}</div>
                  {a.description && (
                    <div style={{ fontSize: 13, color: T.muted, marginTop: 5, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{a.description}</div>
                  )}
                  <div style={{ fontSize: 11.5, color: T.faint, marginTop: 8, fontWeight: 500 }}>{a.source}</div>
                </Card>
              </a>
            );
          })}
        </div>
      )}
    </>,
  );
}
