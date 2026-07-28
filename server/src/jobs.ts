/** CLI entrypoints: `npm run backfill | eod | analytics` */
import { config } from './config.ts';
import { backfill, runEodIngest, latestAvailableSession } from './ingest/nse.ts';
import { runAnalytics } from './analytics/engine.ts';

const cmd = process.argv[2];

async function main(): Promise<void> {
  if (cmd === 'backfill') {
    await backfill(config.backfillSessions);
    await runAnalytics();
  } else if (cmd === 'eod') {
    const date = process.argv[3] ?? await latestAvailableSession();
    if (!date) throw new Error('no recent session found');
    const ok = await runEodIngest(date);
    console.log('[eod]', date, ok ? 'ingested' : 'file not available');
    if (ok) await runAnalytics();
  } else if (cmd === 'analytics') {
    await runAnalytics();
  } else {
    console.log('usage: node src/jobs.ts <backfill|eod [date]|analytics>');
    process.exitCode = 1;
  }
}

main().catch(err => {
  console.error(err);
  process.exitCode = 1;
});
