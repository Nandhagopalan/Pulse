/**
 * Vercel entry point — the entire backend behind one function.
 *
 * The router already speaks `(IncomingMessage, ServerResponse)`, which is what
 * a Vercel Node function is handed, so nothing has to be re-implemented: this
 * file is an adapter, not a second server. `vercel.json` rewrites `/api/*`,
 * `/auth/*` and `/health` here; every other path is a static file from `dist`.
 *
 * The router is built at module scope so it is constructed once per instance
 * and reused across invocations, alongside the pg pool in `db.ts`.
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Router } from '../server/src/router.ts';

/*
 * TEMPORARY DIAGNOSTIC — remove once the runtime is confirmed healthy.
 * Loaded lazily so a module-level failure can be reported over HTTP instead of
 * collapsing into an opaque FUNCTION_INVOCATION_FAILED with no accessible log.
 * The detail is gated behind ?__diag=1 so stack traces are not served to the
 * open internet.
 */
let router: Router | null = null;
let bootError: unknown = null;

async function getRouter(): Promise<Router | null> {
  if (router || bootError) return router;
  try {
    const { buildRouter } = await import('../server/src/app.ts');
    router = buildRouter();
  } catch (err) {
    bootError = err;
    console.error('[pulse] failed to load the application:', err);
  }
  return router;
}

/**
 * A rewrite hands the function its *destination* URL, so the path the client
 * actually asked for is passed along in `__path` (see vercel.json) and restored
 * here before routing. Vercel has also been known to preserve the original URL
 * instead; then `__path` is simply absent and `req.url` is already right. Both
 * cases are correct, which is the point of doing it this way.
 */
function originalUrl(req: IncomingMessage): string {
  const url = new URL(req.url ?? '/', 'http://localhost');
  const path = url.searchParams.get('__path');
  if (!path) return req.url ?? '/';
  url.searchParams.delete('__path');
  const qs = url.searchParams.toString();
  return path + (qs ? '?' + qs : '');
}

export default async function handler(req: IncomingMessage, res: ServerResponse): Promise<void> {
  req.url = originalUrl(req);
  const r = await getRouter();
  if (!r) {
    const wantsDetail = new URL(req.url, 'http://localhost').searchParams.get('__diag') === '1';
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      error: 'boot_failed',
      detail: wantsDetail ? String((bootError as Error)?.stack ?? bootError) : undefined,
    }));
    return;
  }
  await r.dispatch(req, res);
}
