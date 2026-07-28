import type { CSSProperties, ReactNode } from 'react';
import { T } from '../theme';

export function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{ background: T.card, border: '1px solid ' + T.border, borderRadius: T.radius, boxShadow: T.shadow, ...style }}>
      {children}
    </div>
  );
}

export function Label({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: T.faint, ...style }}>
      {children}
    </div>
  );
}

export function Serif({ children, size = 22, style }: { children: ReactNode; size?: number; style?: CSSProperties }) {
  return (
    <div style={{ fontFamily: T.serif, fontSize: size, fontWeight: 600, letterSpacing: '-0.01em', color: T.ink, ...style }}>
      {children}
    </div>
  );
}

export function Mono({ children, size = 13, color = T.ink, weight = 500, style }: {
  children: ReactNode; size?: number; color?: string; weight?: number; style?: CSSProperties;
}) {
  return (
    <span style={{ fontFamily: T.mono, fontSize: size, fontWeight: weight, color, ...style }}>
      {children}
    </span>
  );
}

export function Tag({ children, color = T.navy, bg = T.navySoft }: { children: ReactNode; color?: string; bg?: string }) {
  return (
    <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.05em', padding: '3px 9px', borderRadius: 6, background: bg, color, whiteSpace: 'nowrap' }}>
      {children}
    </span>
  );
}

export function Meter({ pct, color = T.navy, height = 5 }: { pct: number; color?: string; height?: number }) {
  return (
    <div style={{ flex: 1, height, background: T.borderSoft, borderRadius: 99, overflow: 'hidden' }}>
      <div style={{ height: '100%', width: Math.max(0, Math.min(100, pct)) + '%', background: color, borderRadius: 99 }} />
    </div>
  );
}

export function Sparkline({ line, area, color, fill, height, viewW = 100, viewH = 30 }: {
  line: string; area?: string; color: string; fill?: string; height: number; viewW?: number; viewH?: number;
}) {
  return (
    <svg viewBox={`0 0 ${viewW} ${viewH}`} preserveAspectRatio="none" style={{ width: '100%', height, display: 'block', overflow: 'visible' }}>
      {area && <path d={area} fill={fill || 'transparent'} stroke="none" />}
      <path d={line} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

export const ghostBtn: CSSProperties = {
  appearance: 'none', border: 'none', background: 'transparent', cursor: 'pointer',
  fontFamily: T.sans, color: T.muted, padding: '2px 4px', fontSize: 13,
};

export const inkBtn: CSSProperties = {
  appearance: 'none', cursor: 'pointer', fontFamily: T.sans, fontSize: 13, fontWeight: 600,
  padding: '8px 16px', borderRadius: 8, border: 'none', background: T.ink, color: T.card,
};

export const inputStyle: CSSProperties = {
  fontFamily: T.sans, fontSize: 13.5, padding: '8px 12px', border: '1px solid ' + T.border,
  borderRadius: 8, background: T.card, color: T.ink, outline: 'none', width: '100%',
};
