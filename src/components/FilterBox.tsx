import { T } from '../theme';

/**
 * Inline "narrow this table" input.
 *
 * Deliberately not the same thing as the ⌘K palette: this only ever hides rows
 * the tab already holds, while the palette reaches the whole universe. Tabs
 * whose pool is a strict subset (Highs, Watchlist) can filter but can never
 * surface a symbol that is not in that subset — which is why both exist.
 */
export function FilterBox({ value, onChange, placeholder, count, total, width = 190 }: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  /** Rows after filtering, shown so an empty table is never a mystery. */
  count?: number;
  total?: number;
  width?: number;
}) {
  const active = value.trim().length > 0;
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, height: 34, padding: '0 6px 0 10px', borderRadius: 8, border: '1px solid ' + (active ? T.faint : T.border), background: T.card, width, maxWidth: '100%' }}>
      <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={T.faint} strokeWidth={2} strokeLinecap="round" style={{ flexShrink: 0 }}>
        <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
      </svg>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Escape' && active) { e.stopPropagation(); onChange(''); } }}
        placeholder={placeholder ?? 'Filter symbol or sector'}
        aria-label={placeholder ?? 'Filter symbol or sector'}
        style={{ flex: 1, minWidth: 0, appearance: 'none', border: 'none', outline: 'none', background: 'transparent', fontFamily: T.sans, fontSize: 13, color: T.ink }}
      />
      {active && (
        <>
          {count !== undefined && total !== undefined && (
            <span style={{ fontFamily: T.mono, fontSize: 10.5, color: T.faint, flexShrink: 0 }}>{count}/{total}</span>
          )}
          <button
            onClick={() => onChange('')}
            title="Clear filter"
            aria-label="Clear filter"
            style={{ appearance: 'none', border: 0, background: 'transparent', cursor: 'pointer', color: T.faint, fontSize: 15, lineHeight: 1, padding: '6px 4px', margin: '-6px 0', flexShrink: 0 }}
          >
            ×
          </button>
        </>
      )}
    </div>
  );
}
