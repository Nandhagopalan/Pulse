// Every breadth chart plots the tail of a shared session-date axis. These
// helpers turn "the last N bars" into the actual dates those bars cover, so a
// chart never shows an unlabelled run of bars.

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "12 Mar" — compact axis/label form. */
export function fmtDay(iso: string): string {
  const [, m, d] = iso.split('-');
  const mi = Number(m) - 1;
  if (!m || Number.isNaN(mi) || !MON[mi]) return iso;
  return Number(d) + ' ' + MON[mi];
}

/** "12 Mar 2026" — used where the year matters (as-of stamps). */
export function fmtDayYear(iso: string): string {
  const [y] = iso.split('-');
  return fmtDay(iso) + (y ? ' ' + y : '');
}

/** The last `n` dates of the axis; [] when the axis is unavailable. */
export function tail(dates: string[], n: number): string[] {
  if (!dates.length) return [];
  return dates.slice(Math.max(0, dates.length - n));
}

/** "12 Mar → 8 Aug" for the window a chart of `n` bars actually covers. */
export function rangeLabel(dates: string[], n: number): string {
  const t = tail(dates, n);
  if (!t.length) return '';
  return fmtDay(t[0]) + ' → ' + fmtDay(t[t.length - 1]);
}

/** Approximate calendar span of `n` NSE sessions, phrased for a trader. */
export function spanLabel(n: number): string {
  if (n <= 5) return '~1 week';
  if (n <= 11) return '~2 weeks';
  if (n <= 26) return '~1 month';
  if (n <= 45) return '~2 months';
  if (n <= 70) return '~3 months';
  if (n <= 130) return '~6 months';
  if (n <= 260) return '~1 year';
  return Math.round(n / 21) + ' months';
}

/** "45 sessions · ~2 months · 12 Mar → 8 Aug" */
export function windowNote(dates: string[], n: number): string {
  const r = rangeLabel(dates, n);
  return n + ' sessions · ' + spanLabel(n) + (r ? ' · ' + r : '');
}
