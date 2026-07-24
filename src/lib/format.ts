export function fmtPct(v: number) {
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

export function fmtPrice(v: number) {
  return v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
