import type { CSSProperties } from 'react';
import ikigaiIcon from '../assets/ikigai-mark.jpeg';
import { T } from '../theme';

export interface NavItem {
  id: string;
  label: string;
  icon: keyof typeof ICONS;
  count?: number;
}

const ICONS = {
  breadth: <><path d="M3 3v18h18" /><path d="m19 9-5 5-4-4-3 3" /></>,
  chart: <><path d="M3 3v18h18" /><rect x="7" y="10" width="3" height="7" /><rect x="12" y="6" width="3" height="11" /><rect x="17" y="13" width="3" height="4" /></>,
  sectors: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  highs: <><polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" /></>,
  draw: <><polyline points="22 17 13.5 8.5 8.5 13.5 2 7" /><polyline points="16 17 22 17 22 11" /></>,
  watch: <path d="M12 2l2.4 7.4H22l-6 4.6 2.3 7.4L12 17l-6.3 4.4L8 14 2 9.4h7.6z" />,
  news: <><path d="M4 4h13a1 1 0 0 1 1 1v13a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2z" /><path d="M18 8h1a1 1 0 0 1 1 1v9a2 2 0 0 1-2 2" /><path d="M8 8h6M8 12h6M8 16h3" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>,
  strategy: <><path d="M3 17l5-5 4 3 5-7" /><path d="M14 6h5v5" /><circle cx="8" cy="12" r="1.4" /><circle cx="12" cy="15" r="1.4" /></>,
  chevron: <polyline points="15 18 9 12 15 6" />,
} as const;

function Icon({ name, size = 15 }: { name: keyof typeof ICONS; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {ICONS[name]}
    </svg>
  );
}

// The signed-in user is shown once, in the TopBar profile menu; the rail used
// to repeat it in a footer, which was redundant.
export function Sidebar({ items, active, onNav, collapsed, onToggle, isMobile, open, onClose }: {
  items: NavItem[];
  active: string;
  onNav: (id: string) => void;
  collapsed: boolean;
  onToggle: () => void;
  /** Phone layout: the rail becomes an overlay drawer over the content. */
  isMobile?: boolean;
  open?: boolean;
  onClose?: () => void;
}) {
  // On a phone the rail is never collapsed-to-icons — it is either off-canvas
  // or fully open, so labels are always readable when it is showing.
  const isCollapsed = isMobile ? false : collapsed;
  const asideStyle: CSSProperties = {
    width: isCollapsed ? 64 : 232,
    flexShrink: 0,
    height: '100vh',
    position: 'sticky',
    top: 0,
    zIndex: 20,
    background: T.card,
    borderRight: '1px solid ' + T.border,
    display: 'flex',
    flexDirection: 'column',
    transition: 'width .2s cubic-bezier(.2,.8,.2,1)',
  };
  const iconBtn: CSSProperties = {
    width: 26, height: 26, border: 0, background: 'transparent', borderRadius: 7,
    color: T.faint, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
  };

  const aside = (
    <aside
      className={isMobile ? 'sidebar-drawer' + (open ? ' open' : '') : undefined}
      style={asideStyle}
      aria-hidden={isMobile && !open ? true : undefined}
    >
      {/* Logo */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: isCollapsed ? 'center' : 'space-between', height: 56, padding: isCollapsed ? 0 : '0 16px', borderBottom: '1px solid ' + T.borderSoft }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src={ikigaiIcon} alt="Ikigai" style={{ width: 30, height: 30, borderRadius: 9, objectFit: 'cover', display: 'block', boxShadow: 'inset 0 0 0 1px ' + T.border, background: T.card, mixBlendMode: 'multiply' }} />
          {!isCollapsed && <span style={{ fontSize: 15, fontWeight: 700, letterSpacing: '-0.01em', color: T.ink }}>Pulse</span>}
        </div>
        {isMobile ? (
          <button style={{ ...iconBtn, width: 34, height: 34, fontSize: 16 }} onClick={onClose} title="Close menu" aria-label="Close menu">✕</button>
        ) : !isCollapsed && (
          <button style={iconBtn} onClick={onToggle} title="Collapse"><Icon name="chevron" size={13} /></button>
        )}
      </div>

      {/* Nav */}
      <nav className="no-scrollbar" style={{ flex: 1, overflowY: 'auto', padding: '12px 8px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {isCollapsed && (
          <button style={{ ...iconBtn, width: 40, height: 40, margin: '0 auto 4px', transform: 'rotate(180deg)' }} onClick={onToggle} title="Expand"><Icon name="chevron" size={13} /></button>
        )}
        {items.map(it => {
          const isActive = it.id === active;
          const btn: CSSProperties = {
            display: 'flex', alignItems: 'center', gap: 12, height: isMobile ? 44 : 38,
            padding: isCollapsed ? 0 : '0 12px', width: isCollapsed ? 40 : '100%',
            justifyContent: isCollapsed ? 'center' : 'flex-start', margin: isCollapsed ? '0 auto' : 0,
            border: 0, background: isActive ? T.brand50 : 'transparent', borderRadius: 9,
            color: isActive ? T.brand600 : T.text, fontWeight: isActive ? 600 : 400,
            fontFamily: T.sans, fontSize: isMobile ? 14 : 13, textAlign: 'left', cursor: 'pointer', transition: '.12s',
          };
          return (
            <button
              key={it.id}
              style={btn}
              title={it.label}
              onClick={() => { onNav(it.id); onClose?.(); }}
              onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = T.cardAlt; }}
              onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
            >
              <Icon name={it.icon} size={15} />
              {!isCollapsed && <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.label}</span>}
              {!isCollapsed && it.count != null && (
                <span style={{ fontSize: 11, color: isActive ? T.brand : T.faint, fontFamily: T.mono }}>{it.count}</span>
              )}
            </button>
          );
        })}
      </nav>
    </aside>
  );

  if (!isMobile) return aside;
  return (
    <>
      {open && <div className="sidebar-scrim" onClick={onClose} aria-hidden="true" />}
      {aside}
    </>
  );
}
