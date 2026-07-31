// Shared IST market-hours helper. Mirrors the server's isMarketOpen()
// (live.ts): NSE cash session is 09:15–15:30 IST, Mon–Fri.
export function marketStatus(now: Date = new Date()) {
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
  const day = ist.getDay();
  const mins = ist.getHours() * 60 + ist.getMinutes();
  const open = day >= 1 && day <= 5 && mins >= 555 && mins <= 930; // 09:15–15:30 IST
  return { open, label: open ? 'Market open' : 'Market closed' };
}

export const isMarketOpen = (now?: Date) => marketStatus(now).open;
