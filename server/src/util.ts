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

/**
 * NSE equity session: Mon–Fri, 09:15–15:30 IST.
 *
 * Purely a status label now. Nothing here polls during market hours: every
 * price served is an end-of-day close published by the nightly pipeline.
 */
export function isMarketOpen(): boolean {
  const dow = istNow().getUTCDay();
  const mins = istMinutes();
  return dow >= 1 && dow <= 5 && mins >= 555 && mins <= 930;
}
