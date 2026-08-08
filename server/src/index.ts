import { createServer } from 'node:http';
import { config } from './config.ts';
import { Router } from './router.ts';
import { registerAuthRoutes } from './auth.ts';
import { registerApiRoutes } from './api.ts';
import { startScheduler, bootCatchup } from './scheduler.ts';
import { getDb } from './db.ts';

const router = new Router();
registerAuthRoutes(router);
registerApiRoutes(router);

router.get('/health', ctx => {
  ctx.res.writeHead(200, { 'Content-Type': 'application/json' });
  ctx.res.end(JSON.stringify({ ok: true }));
});

async function main(): Promise<void> {
  await getDb(); // fail fast if storage is misconfigured
  const server = createServer((req, res) => void router.dispatch(req, res));
  server.listen(config.port, () => {
    console.log(`[pulse-server] listening on http://localhost:${config.port}`);
    console.log(`[pulse-server] Google OAuth redirect URL: ${config.googleRedirectUrl}`);
  });
  startScheduler();
  await bootCatchup();
}

void main();
