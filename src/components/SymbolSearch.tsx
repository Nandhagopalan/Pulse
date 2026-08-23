import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { T, dirColor } from '../theme';
import { Mono } from './ui';
import { stockTag } from './StockTable';
import { fmtPct, fmtPrice } from '../lib/format';
import { MAX_HITS, candidates, searchSymbols, type SearchHit } from '../lib/search';
import type { MarketData } from '../lib/data';
import type { Media } from '../lib/useMedia';

/**
 * Command-palette search over every symbol in the session.
 *
 * Until this existed the only way to reach a stock was to scroll a table and
 * find it, and the Charts tab could only chart an index or something already
 * starred — so a symbol you had not starred was unreachable from the UI.
 *
 * In 'jump' mode Enter opens the detail drawer (indices have none, so they go
 * straight to the chart) and Cmd/Ctrl+Enter charts the selection. In 'chart'
 * mode it is the Charts tab's symbol picker, so everything charts — which is
 * what replaced a <select> that could only offer indices and starred stocks.
 */
export function SymbolSearch({ D, watch, toggle, onOpenStock, onOpenChart, onClose, media, mode = 'jump' }: {
  D: MarketData;
  watch: Record<string, true>;
  toggle: (sym: string) => void;
  onOpenStock: (sym: string) => void;
  onOpenChart: (sym: string) => void;
  onClose: () => void;
  media: Media;
  mode?: 'jump' | 'chart';
}) {
  const { isMobile } = media;
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Arrow keys scroll the list, which drags rows under a stationary pointer and
  // fires mouseenter — without this the hover would keep stealing the
  // highlight back and arrow navigation would look broken.
  const usingKeys = useRef(false);

  const pool = useMemo(() => candidates(D), [D]);
  const hits = useMemo(() => searchSymbols(pool, q, watch), [pool, q, watch]);

  // A shrinking result list must never leave the highlight past the end.
  const idx = hits.length ? Math.min(active, hits.length - 1) : 0;
  const sel: SearchHit | undefined = hits[idx];

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => { setActive(0); }, [q]);
  useEffect(() => { rowRefs.current[idx]?.scrollIntoView({ block: 'nearest' }); }, [idx]);

  useEffect(() => {
    const onMove = () => { usingKeys.current = false; };
    window.addEventListener('mousemove', onMove);
    return () => window.removeEventListener('mousemove', onMove);
  }, []);

  // Escape closes from anywhere in the overlay, not just the input.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const open = (hit: SearchHit) => {
    // As a picker every result charts; otherwise a stock opens its drawer and
    // an index — which has no stock row behind it — goes to the chart.
    if (mode === 'chart' || hit.kind === 'index') onOpenChart(hit.key);
    else onOpenStock(hit.key);
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      usingKeys.current = true;
      setActive(i => Math.min(i + 1, hits.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      usingKeys.current = true;
      setActive(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && sel) {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) onOpenChart(sel.key); else open(sel);
    }
  };

  const panel = isMobile
    ? { position: 'fixed' as const, left: 8, right: 8, top: 8, bottom: 8, display: 'flex', flexDirection: 'column' as const }
    : { position: 'fixed' as const, left: '50%', top: 84, transform: 'translateX(-50%)', width: 'min(620px, calc(100vw - 32px))', maxHeight: 'min(560px, calc(100vh - 120px))', display: 'flex', flexDirection: 'column' as const };

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgba(35,43,56,0.28)', zIndex: 60, animation: 'fade-in 120ms ease' }}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={mode === 'chart' ? 'Chart a symbol' : 'Jump to symbol'}
        style={{
          ...panel, zIndex: 61, background: T.card, borderRadius: 14, overflow: 'hidden',
          border: '1px solid ' + T.border, boxShadow: T.shadowPop, animation: 'fade-in 120ms ease',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: isMobile ? '12px 14px' : '13px 16px', borderBottom: '1px solid ' + T.borderSoft, flexShrink: 0 }}>
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={T.faint} strokeWidth={2} strokeLinecap="round" style={{ flexShrink: 0 }}>
            <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            ref={inputRef}
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={mode === 'chart' ? 'Chart any symbol…' : 'Jump to symbol…'}
            aria-label={mode === 'chart' ? 'Chart any symbol' : 'Jump to symbol'}
            style={{
              flex: 1, minWidth: 0, appearance: 'none', border: 'none', outline: 'none', background: 'transparent',
              fontFamily: T.sans, color: T.ink,
              // iOS zooms the viewport on focus below 16px, and this field
              // takes focus the moment the palette opens.
              fontSize: isMobile ? 16 : 15,
            }}
          />
          <button
            onClick={onClose}
            aria-label="Close search"
            style={{ appearance: 'none', border: 0, background: T.cardAlt, boxShadow: 'inset 0 0 0 1px ' + T.border, borderRadius: 6, cursor: 'pointer', color: T.muted, fontFamily: T.sans, fontSize: 11, fontWeight: 600, padding: isMobile ? '7px 10px' : '4px 7px', flexShrink: 0 }}
          >
            {isMobile ? 'Close' : 'ESC'}
          </button>
        </div>

        <div className="no-scrollbar" style={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
          {hits.length === 0 && (
            <div style={{ padding: '32px 18px', textAlign: 'center', fontSize: 13.5, color: T.muted }}>
              No symbol matches <Mono size={13} weight={600}>{q}</Mono>
              <div style={{ fontSize: 12, color: T.faint, marginTop: 6 }}>
                Only symbols that traded in the last session are searchable.
              </div>
            </div>
          )}

          {!q && hits.length > 0 && (
            <div style={{ padding: '9px 16px 5px', fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: T.faint }}>
              Indices &amp; your watchlist
            </div>
          )}

          {hits.map((hit, i) => {
            const on = i === idx;
            const starred = hit.kind === 'stock' && !!watch[hit.key];
            const chg = hit.kind === 'stock' ? hit.stock.chg1d : hit.index.chgPct;
            const price = hit.kind === 'stock' ? hit.stock.price : hit.index.value;
            return (
              <div
                key={hit.kind + ':' + hit.key}
                ref={el => { rowRefs.current[i] = el; }}
                onClick={() => open(hit)}
                onMouseEnter={() => { if (!usingKeys.current) setActive(i); }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
                  padding: isMobile ? '11px 14px' : '9px 16px',
                  background: on ? T.cardAlt : 'transparent',
                  borderBottom: '1px solid ' + T.borderSoft,
                }}
              >
                {hit.kind === 'stock' ? (
                  <button
                    onClick={e => { e.stopPropagation(); toggle(hit.key); }}
                    title="Watchlist"
                    aria-label={starred ? 'Remove from watchlist' : 'Add to watchlist'}
                    style={{ appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15, lineHeight: 1, color: starred ? T.amber : T.border, padding: '7px 6px', margin: '-7px -6px', flexShrink: 0 }}
                  >
                    {starred ? '★' : '☆'}
                  </button>
                ) : (
                  <span style={{ width: 15, flexShrink: 0 }} />
                )}

                <div style={{ minWidth: 0, flex: 1 }}>
                  <Mono size={13} weight={600} style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{hit.key}</Mono>
                  <div style={{ fontSize: 11.5, color: T.faint, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{hit.sub}</div>
                </div>

                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <Mono size={12.5} style={{ display: 'block' }}>{fmtPrice(price)}</Mono>
                  <Mono size={11} weight={600} color={dirColor(chg)} style={{ display: 'block', marginTop: 1 }}>{fmtPct(chg)}</Mono>
                </div>

                {/* The tag needs room the phone layout does not have. */}
                {!isMobile && hit.kind === 'stock' && (
                  <div style={{ width: 92, textAlign: 'right', flexShrink: 0 }}>{stockTag(hit.stock)}</div>
                )}

                <button
                  onClick={e => { e.stopPropagation(); onOpenChart(hit.key); }}
                  title="Open chart"
                  aria-label={'Chart ' + hit.key}
                  style={{ appearance: 'none', border: 0, background: 'transparent', cursor: 'pointer', color: on ? T.navy : T.faint, padding: '7px 4px', margin: '-7px -4px', display: 'inline-flex', flexShrink: 0 }}
                >
                  <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 3v18h18" /><path d="M7 14l4-4 3 3 5-6" />
                  </svg>
                </button>
              </div>
            );
          })}
        </div>

        {!isMobile && (
          <div style={{ display: 'flex', gap: 14, padding: '8px 16px', borderTop: '1px solid ' + T.borderSoft, background: T.cardAlt, fontSize: 11, color: T.faint, flexShrink: 0 }}>
            <span>↑↓ move</span>
            <span>↵ {mode === 'chart' ? 'chart' : 'open'}</span>
            {mode === 'jump' && <span>⌘↵ chart</span>}
            <span>esc close</span>
            <div style={{ flex: 1 }} />
            <span>{hits.length}{hits.length === MAX_HITS ? '+' : ''} of {D.stocks.length.toLocaleString('en-IN')}</span>
          </div>
        )}
      </div>
    </>
  );
}
