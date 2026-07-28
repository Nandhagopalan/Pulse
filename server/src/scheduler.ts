/**
 * IST-aware operational schedule (per the sourcing spec):
 *   08:30  master/constituent sync (Mondays) + FII/DII retry
 *   09:15–15:30  live index quote polling (20s cadence)
 *   18:45  EOD ingest chain (bhavcopy, MTO, index closes, FII/DII)
 *   19:30  analytics engine (runs automatically right after a successful ingest)
 * Plus boot-time catch-up: bootstrap backfill on an empty DB, or re-ingest any
 * missed session, then recompute analytics if stale.
 */
import { config } from './config.ts';
import { metaGet } from './db.ts';
import { backfill, backfillProgress, ingestFiiDii, ingestIndexConstituents, latestAvailableSession, runEodIngest } from './ingest/nse.ts';
import { runAnalytics } from './analytics/engine.ts';
import { pollIndexQuotes, isMarketOpen } from './live.ts';
import { istMinutes, istNow, istToday, isWeekend } from './util.ts';

let eodRanFor = '';        // session date the EOD chain last ran for
let preMarketRanFor = '';
let busy = false;

async function guarded(name: string, fn: () => Promise<unknown>): Promise<void> {
  if (busy) return;
  busy = true;
  try {
    await fn();
  } catch (err) {
    console.error(`[scheduler] ${name} failed:`, err);
  } finally {
    busy = false;
  }
}

async function tick(): Promise<void> {
  const today = istToday();
  const mins = istMinutes();
  const weekday = !isWeekend(today);

  if (isMarketOpen()) await pollIndexQuotes();

  // Pre-market reference sync (constituents weekly, Monday).
  if (weekday && mins >= 510 && preMarketRanFor !== today) {
    preMarketRanFor = today;
    if (istNow().getUTCDay() === 1) await guarded('constituents', ingestIndexConstituents);
  }

  // EOD chain — NSE publishes files 18:00–19:30 IST; we start at 18:45 and
  // retry every tick until the bhavcopy shows up.
  if (weekday && mins >= 1125 && eodRanFor !== today) {
    await guarded('eod', async () => {
      const ok = await runEodIngest(today);
      if (ok) {
        eodRanFor = today;
        await runAnalytics();
        console.log('[scheduler] EOD pipeline complete for', today);
      }
    });
  }
}

export async function bootCatchup(): Promise<void> {
  const last = await metaGet('last_ingested_session');
  if (!last) {
    console.log(`[boot] empty database — starting bootstrap backfill of ${config.backfillSessions} sessions`);
    // Run in background; /api/status reports progress.
    void backfill(config.backfillSessions)
      .then(() => runAnalytics())
      .then(() => console.log('[boot] bootstrap backfill + analytics complete'))
      .catch(err => console.error('[boot] backfill failed:', err));
    return;
  }

  // Missed session catch-up (e.g. server was off during the EOD window).
  const available = await latestAvailableSession();
  if (available && available > last) {
    console.log(`[boot] catching up EOD ingest ${last} → ${available}`);
    await guarded('catchup', async () => {
      await runEodIngest(available);
      await runAnalytics();
    });
    eodRanFor = available;
  } else {
    const analyticsDate = await metaGet('last_analytics_date');
    if (analyticsDate !== last) {
      console.log('[boot] analytics stale — recomputing for', last);
      await guarded('analytics', runAnalytics);
    }
    eodRanFor = last === istToday() ? last : '';
  }
  void ingestFiiDii();
}

export function startScheduler(): void {
  const liveInterval = 20_000;
  setInterval(() => { void tick(); }, liveInterval);
  console.log('[scheduler] started (20s tick, IST schedule)');
}

export { backfillProgress };
