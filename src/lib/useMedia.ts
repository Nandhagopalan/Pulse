import { useEffect, useState } from 'react';

/**
 * Breakpoint flags for a UI built almost entirely out of inline styles.
 *
 * Inline styles cannot carry media queries, so the layout decisions that would
 * normally live in CSS (how many columns, whether the sidebar is a drawer) are
 * taken in render from these flags instead. Everything that *can* be expressed
 * in plain CSS — scroll containers, the drawer transform — lives in index.css.
 */

export const BP = {
  /** Phones: single column everywhere, sidebar becomes an overlay drawer. */
  mobile: 768,
  /** Small laptops / tablets: two columns instead of three, sidebar collapsed. */
  tablet: 1080,
} as const;

function query(q: string): boolean {
  return typeof window !== 'undefined' && window.matchMedia(q).matches;
}

function useMediaQuery(q: string): boolean {
  const [matches, setMatches] = useState(() => query(q));
  useEffect(() => {
    const mql = window.matchMedia(q);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [q]);
  return matches;
}

export interface Media {
  /** ≤768px — phone layout. */
  isMobile: boolean;
  /** ≤1080px but wider than a phone — narrow desktop / tablet layout. */
  isTablet: boolean;
  /** ≤1080px — anything that is not a roomy desktop. */
  isNarrow: boolean;
  /**
   * ≤920px — not enough header room for the clock and session pills beside the
   * title. A landscape phone is wider than `mobile` but still cannot hold them.
   */
  isTight: boolean;
}

export function useMedia(): Media {
  const isMobile = useMediaQuery(`(max-width: ${BP.mobile}px)`);
  const isNarrow = useMediaQuery(`(max-width: ${BP.tablet}px)`);
  const isTight = useMediaQuery('(max-width: 920px)');
  return { isMobile, isTablet: isNarrow && !isMobile, isNarrow, isTight };
}

/** `n` columns on desktop, fewer as the viewport narrows. */
export function cols(m: Media, desktop: number, tablet: number, mobile: number): string {
  const n = m.isMobile ? mobile : m.isTablet ? tablet : desktop;
  return `repeat(${n}, minmax(0, 1fr))`;
}
