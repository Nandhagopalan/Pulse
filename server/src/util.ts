/** Date helpers — all market logic runs on IST calendar dates formatted as YYYY-MM-DD. */

const IST_OFFSET_MS = 5.5 * 3600 * 1000;

export function istNow(): Date {
  return new Date(Date.now() + IST_OFFSET_MS);
}

/** Current IST time as fractional minutes since midnight. */
export function istMinutes(): number {
  const d = istNow();
  return d.getUTCHours() * 60 + d.getUTCMinutes();
}

export function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Today's IST calendar date. */
export function istToday(): string {
  return isoDate(istNow());
}

export function addDays(iso: string, days: number): string {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return isoDate(d);
}

export function isWeekend(iso: string): boolean {
  const dow = new Date(iso + 'T00:00:00Z').getUTCDay();
  return dow === 0 || dow === 6;
}

/** NSE equity session: Mon–Fri, 09:15–15:30 IST. */
export function isMarketOpen(): boolean {
  const dow = istNow().getUTCDay();
  const mins = istMinutes();
  return dow >= 1 && dow <= 5 && mins >= 555 && mins <= 930;
}

/** YYYY-MM-DD → YYYYMMDD */
export function compact(iso: string): string {
  return iso.replaceAll('-', '');
}

/** YYYY-MM-DD → DDMMYYYY (used by MTO / index close file names) */
export function ddmmyyyy(iso: string): string {
  const [y, m, d] = iso.split('-');
  return d + m + y;
}

export function sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms));
}

const BROWSER_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
  'Accept': '*/*',
  'Accept-Language': 'en-US,en;q=0.9',
};

/** Fetch with browser-like headers and a timeout; returns null on 404. Throws on other failures. */
export async function fetchBytes(url: string, timeoutMs = 30000): Promise<Buffer | null> {
  const res = await fetch(url, { headers: BROWSER_HEADERS, signal: AbortSignal.timeout(timeoutMs), redirect: 'follow' });
  if (res.status === 404 || res.status === 403) return null;
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

/** Minimal CSV parser (handles quoted fields). Returns array of rows. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], field = '', inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false;
      } else field += ch;
    } else if (ch === '"') inQ = true;
    else if (ch === ',') { row.push(field); field = ''; }
    else if (ch === '\n') { row.push(field); field = ''; if (row.length > 1 || row[0] !== '') rows.push(row); row = []; }
    else if (ch !== '\r') field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows;
}

/** Index CSV rows by header name (case/space-insensitive). */
export function csvObjects(rows: string[][]): Record<string, string>[] {
  if (!rows.length) return [];
  const headers = rows[0].map(h => h.trim().toLowerCase().replace(/\s+/g, '_'));
  return rows.slice(1).map(r => {
    const o: Record<string, string> = {};
    headers.forEach((h, i) => { o[h] = (r[i] ?? '').trim(); });
    return o;
  });
}
