/**
 * User SSO via Zerodha Kite Connect.
 *
 * Flow: /auth/kite/login → kite.zerodha.com login → redirect back to
 * KITE_REDIRECT_URL (?request_token=...) → exchange for access_token →
 * upsert user, create session row, set HttpOnly cookie.
 *
 * The freshest Kite access token is also stored in `meta` so backend jobs
 * (instrument sync, quotes) can reuse it. Kite tokens expire daily (~6 AM IST).
 */
import { randomBytes } from 'node:crypto';
import { getDb, metaGet, metaSet } from './db.ts';
import { config } from './config.ts';
import { exchangeToken, loginUrl, KiteError } from './kite.ts';
import { json, redirect, setCookie, type Ctx, type Router } from './router.ts';

const COOKIE = 'pulse_sid';
const SESSION_TTL_H = 24; // Kite tokens die daily anyway

export async function resolveSession(ctx: Ctx): Promise<boolean> {
  const sid = ctx.cookies[COOKIE];
  if (!sid) return false;
  const db = await getDb();
  const rows = await db.all<{ sid: string; user_id: string; access_token: string | null; expires_at: string }>(
    'SELECT sid, user_id, access_token, expires_at FROM sessions WHERE sid = ?', [sid],
  );
  const s = rows[0];
  if (!s || s.expires_at < new Date().toISOString()) return false;
  ctx.session = { sid: s.sid, userId: s.user_id, accessToken: s.access_token };
  return true;
}

export function requireAuth(handler: (ctx: Ctx) => Promise<void> | void) {
  return async (ctx: Ctx) => {
    if (!(await resolveSession(ctx))) return json(ctx, 401, { error: 'unauthorized' });
    return handler(ctx);
  };
}

async function createSession(ctx: Ctx, userId: string, accessToken: string | null): Promise<void> {
  const db = await getDb();
  const sid = randomBytes(32).toString('base64url');
  const now = new Date();
  const expires = new Date(now.getTime() + SESSION_TTL_H * 3600 * 1000);
  await db.run('INSERT INTO sessions (sid, user_id, access_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)', [
    sid, userId, accessToken, now.toISOString(), expires.toISOString(),
  ]);
  setCookie(ctx, COOKIE, sid, SESSION_TTL_H * 3600);
}

/** Most recent Kite access token across all logins — used by ingestion jobs. */
export async function serviceAccessToken(): Promise<string | null> {
  return metaGet('kite_access_token');
}

export function registerAuthRoutes(router: Router): void {
  router.get('/auth/kite/login', ctx => {
    redirect(ctx, loginUrl());
  });

  router.get('/auth/kite/callback', async ctx => {
    const status = ctx.url.searchParams.get('status');
    const requestToken = ctx.url.searchParams.get('request_token');
    if (status === 'cancelled' || !requestToken) return redirect(ctx, config.appUrl + '?login=cancelled');
    try {
      const s = await exchangeToken(requestToken);
      const db = await getDb();
      const userId = 'kite:' + s.user_id;
      await db.run(
        `INSERT INTO users (id, provider, name, email, avatar, created_at) VALUES (?, 'kite', ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET name = excluded.name, email = excluded.email, avatar = excluded.avatar`,
        [userId, s.user_name, s.email, s.avatar_url, new Date().toISOString()],
      );
      await createSession(ctx, userId, s.access_token);
      await metaSet('kite_access_token', s.access_token);
      await metaSet('kite_access_token_at', new Date().toISOString());
      redirect(ctx, config.appUrl);
    } catch (err) {
      const msg = err instanceof KiteError ? err.message : 'token_exchange_failed';
      console.error('[auth] kite callback failed:', err);
      redirect(ctx, config.appUrl + '?login=error&reason=' + encodeURIComponent(msg));
    }
  });

  router.get('/auth/me', async ctx => {
    if (!(await resolveSession(ctx))) return json(ctx, 401, { error: 'unauthorized' });
    const db = await getDb();
    const rows = await db.all<{ id: string; name: string; email: string; avatar: string | null }>(
      'SELECT id, name, email, avatar FROM users WHERE id = ?', [ctx.session!.userId],
    );
    if (!rows[0]) return json(ctx, 401, { error: 'unauthorized' });
    json(ctx, 200, { user: rows[0], kiteConnected: !!ctx.session!.accessToken });
  });

  router.post('/auth/logout', async ctx => {
    const sid = ctx.cookies[COOKIE];
    if (sid) {
      const db = await getDb();
      await db.run('DELETE FROM sessions WHERE sid = ?', [sid]);
    }
    setCookie(ctx, COOKIE, '', 0);
    json(ctx, 200, { ok: true });
  });

  // Local development bypass (enable with DEV_LOGIN=1) — lets the terminal run
  // on ingested data without a live Kite login. Never enable in production.
  if (config.devLogin) {
    router.get('/auth/dev-login', async ctx => {
      const db = await getDb();
      await db.run(
        `INSERT INTO users (id, provider, name, email, avatar, created_at) VALUES ('dev:local', 'dev', 'Ikigai Trader', 'dev@local', NULL, ?)
         ON CONFLICT(id) DO NOTHING`,
        [new Date().toISOString()],
      );
      await createSession(ctx, 'dev:local', null);
      redirect(ctx, config.appUrl);
    });
  }
}
