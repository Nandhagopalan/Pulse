export function areaLine(pts: number[], w: number, h: number, pt = 6, pb = 8) {
  const min = Math.min(...pts), max = Math.max(...pts), rg = (max - min) || 1;
  const st = w / (pts.length - 1);
  const xy = pts.map((p, i) => [i * st, pt + (h - pt - pb) * (1 - (p - min) / rg)]);
  const line = xy.map((c, i) => (i ? 'L' : 'M') + c[0].toFixed(1) + ' ' + c[1].toFixed(1)).join(' ');
  return { line, area: line + ' L' + w + ' ' + h + ' L0 ' + h + ' Z' };
}

export function barsBottom(vals: number[], w: number, h: number, pad = 8) {
  const max = Math.max(...vals, 1); const step = w / vals.length; const bw = step * 0.62; let d = '';
  vals.forEach((v, i) => { const bh = Math.max(1, (v / max) * (h - pad * 2)); const x = i * step + (step - bw) / 2; const y = h - pad - bh; d += 'M' + x.toFixed(1) + ' ' + y.toFixed(1) + ' h' + bw.toFixed(1) + ' v' + bh.toFixed(1) + ' h' + (-bw).toFixed(1) + ' Z '; });
  return d;
}

export function barsTop(vals: number[], w: number, h: number, pad = 8) {
  const max = Math.max(...vals, 1); const step = w / vals.length; const bw = step * 0.62; let d = '';
  vals.forEach((v, i) => { const bh = Math.max(1, (v / max) * (h - pad * 2)); const x = i * step + (step - bw) / 2; d += 'M' + x.toFixed(1) + ' ' + pad + ' h' + bw.toFixed(1) + ' v' + bh.toFixed(1) + ' h' + (-bw).toFixed(1) + ' Z '; });
  return d;
}

export function barsSigned(vals: number[], w: number, h: number, pad = 6) {
  const m = Math.max(...vals.map(Math.abs), 1); const zy = h / 2; const step = w / vals.length; const bw = step * 0.58; let pos = '', neg = '';
  vals.forEach((v, i) => { const x = i * step + (step - bw) / 2; const bh = Math.abs(v) / m * (h / 2 - pad); if (v >= 0) pos += 'M' + x.toFixed(1) + ' ' + (zy - bh).toFixed(1) + ' h' + bw.toFixed(1) + ' v' + bh.toFixed(1) + ' h' + (-bw).toFixed(1) + ' Z '; else neg += 'M' + x.toFixed(1) + ' ' + zy.toFixed(1) + ' h' + bw.toFixed(1) + ' v' + bh.toFixed(1) + ' h' + (-bw).toFixed(1) + ' Z '; });
  return { pos, neg, zy: zy.toFixed(1) };
}

export function polar(cx: number, cy: number, r: number, ang: number): [number, number] {
  const a = (ang - 90) * Math.PI / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

export function arc(cx: number, cy: number, r: number, a0: number, a1: number) {
  const s = polar(cx, cy, r, a0), e = polar(cx, cy, r, a1);
  const large = (a1 - a0) > 180 ? 1 : 0;
  return 'M ' + s[0].toFixed(1) + ' ' + s[1].toFixed(1) + ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 ' + e[0].toFixed(1) + ' ' + e[1].toFixed(1);
}

export function strHash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

export function ema(a: number[], p: number) {
  const k = 2 / (p + 1);
  const o: number[] = [];
  let e = a[0];
  a.forEach((v, i) => { e = i ? v * k + e * (1 - k) : v; o.push(e); });
  return o;
}
