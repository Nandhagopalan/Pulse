/**
 * The application, assembled — routes only, no transport.
 *
 * Two things start Pulse: `index.ts` (a long-lived Node process, for local
 * development) and `api/pulse.ts` (one Vercel function). Both must serve
 * exactly the same routes, so the route table lives here rather than in either
 * entry point.
 */
import { Router } from './router.ts';
import { registerAuthRoutes } from './auth.ts';
import { registerApiRoutes } from './api.ts';

export function buildRouter(): Router {
  const router = new Router();
  registerAuthRoutes(router);
  registerApiRoutes(router);

  // Unauthenticated liveness. Deliberately says nothing about the database:
  // this answers "is the code running", and /api/status answers the rest.
  router.get('/health', ctx => {
    ctx.res.writeHead(200, { 'Content-Type': 'application/json' });
    ctx.res.end(JSON.stringify({ ok: true }));
  });

  return router;
}
