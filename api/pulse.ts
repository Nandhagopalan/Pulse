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
 *
 * This file imports across a directory boundary, which the deployment has to be
 * told about twice: `tsconfig.json` at the repo root carries the compiler
 * options that let the type-checker accept `.ts` specifiers, and `includeFiles`
 * in vercel.json copies `server/src` into the bundle, because the dependency
 * tracer does not follow those specifiers out of `api/`. Without either one the
 * static build still succeeds and every function route fails at load.
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import { buildRouter } from '../server/src/app.ts';

const router = buildRouter();

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

export default function handler(req: IncomingMessage, res: ServerResponse): void {
  req.url = originalUrl(req);
  void router.dispatch(req, res);
}
