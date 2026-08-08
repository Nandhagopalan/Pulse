/**
 * User SSO via Google.
 *
 * Flow: /auth/google/login → Google consent → redirect back to
 * GOOGLE_REDIRECT_URL (?code=&state=) → exchange for an access token → read
 * the profile → upsert user, create session row, set HttpOnly cookie.
 *
 * Identity is the only thing sessions carry. Pulse holds no broker credential
 * of any kind — see docs/multi_user_deployment_proposal.md.
 */
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { getDb } from './db.ts';
import { config } from './config.ts';
import { exchangeCode, getProfile, loginUrl as googleLoginUrl, GoogleError } from './google.ts';
import { json, redirect, setCookie, type Ctx, type Router } from './router.ts';

const COOKIE = 'pulse_sid';
const STATE_COOKIE = 'pulse_oauth_state';
const SESSION_TTL_H = 24 * 30; // identity only — nothing expires alongside it
const STATE_TTL_S = 600;       // consent screens don't take ten minutes

export async function resolveSession(ctx: Ctx): Promise<boolean> {
  const sid = ctx.cookies[COOKIE];
  if (!sid) return false;
  const db = await getDb();
  const rows = await db.all<{ sid: string; user_id: string; expires_at: string }>(
    'SELECT sid, user_id, expires_at FROM sessions WHERE sid = ?', [sid],
  );
  const s = rows[0];
  if (!s || s.expires_at < new Date().toISOString()) return false;
  ctx.session = { sid: s.sid, userId: s.user_id };
  return true;
}

export function requireAuth(handler: (ctx: Ctx) => Promise<void> | void) {
  return async (ctx: Ctx) => {
    if (!(await resolveSession(ctx))) return json(ctx, 401, { error: 'unauthorized' });
    return handler(ctx);
  };
}

async function createSession(ctx: Ctx, userId: string): Promise<void> {
  const db = await getDb();
  const sid = randomBytes(32).toString('base64url');
  const now = new Date();
  const expires = new Date(now.getTime() + SESSION_TTL_H * 3600 * 1000);
  await db.run('INSERT INTO sessions (sid, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)', [
    sid, userId, now.toISOString(), expires.toISOString(),
  ]);
  setCookie(ctx, COOKIE, sid, SESSION_TTL_H * 3600);
}

/**
 * Access control. Closed by default so a deployed instance is never
 * accidentally open to every Google account on earth; see config.allowedEmails.
 */
function emailAllowed(email: string): boolean {
  if (config.allowAllSignups) return true;
  return config.allowedEmails.has(email.trim().toLowerCase());
}

/** Constant-time compare for the OAuth state token. */
function sameToken(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function registerAuthRoutes(router: Router): void {
  router.get('/auth/google/login', ctx => {
    // One-time state, echoed back by Google and compared against the cookie —
    // without it, an attacker can complete the flow in a victim's browser.
    const state = randomBytes(16).toString('base64url');
    setCookie(ctx, STATE_COOKIE, state, STATE_TTL_S);
    redirect(ctx, googleLoginUrl(state));
  });

  router.get('/auth/google/callback', async ctx => {
    const fail = (reason: string) => {
      setCookie(ctx, STATE_COOKIE, '', 0);
      redirect(ctx, config.appUrl + '?login=error&reason=' + encodeURIComponent(reason));
    };

    if (ctx.url.searchParams.get('error')) return fail('cancelled');
    const code = ctx.url.searchParams.get('code');
    const state = ctx.url.searchParams.get('state');
    const expected = ctx.cookies[STATE_COOKIE];
    if (!code) return fail('missing_code');
    if (!state || !expected || !sameToken(state, expected)) return fail('bad_state');

    try {
      const { access_token } = await exchangeCode(code);
      const p = await getProfile(access_token);
      // An unverified Google email is not proof of anything, and it is what the
      // allowlist is keyed on.
      if (!p.email || !p.email_verified) return fail('email_unverified');
      if (!emailAllowed(p.email)) {
        setCookie(ctx, STATE_COOKIE, '', 0);
        return redirect(ctx, config.appUrl + '?login=denied');
      }

      const db = await getDb();
      // Keyed on the Google subject id, which is stable — emails get reassigned.
      const userId = 'google:' + p.sub;
      await db.run(
        `INSERT INTO users (id, provider, name, email, avatar, created_at) VALUES (?, 'google', ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET name = excluded.name, email = excluded.email, avatar = excluded.avatar`,
        [userId, p.name, p.email, p.picture, new Date().toISOString()],
      );
      await createSession(ctx, userId);
      setCookie(ctx, STATE_COOKIE, '', 0);
      redirect(ctx, config.appUrl);
    } catch (err) {
      // Never surface the raw provider message to the browser — it can carry
      // request details. The log gets the full error.
      console.error('[auth] google callback failed:', err);
      fail(err instanceof GoogleError ? 'google_' + err.status : 'sign_in_failed');
    }
  });

  router.get('/auth/me', async ctx => {
    if (!(await resolveSession(ctx))) return json(ctx, 401, { error: 'unauthorized' });
    const db = await getDb();
    const rows = await db.all<{ id: string; provider: string; name: string; email: string; avatar: string | null }>(
      'SELECT id, provider, name, email, avatar FROM users WHERE id = ?', [ctx.session!.userId],
    );
    if (!rows[0]) return json(ctx, 401, { error: 'unauthorized' });
    json(ctx, 200, { user: rows[0] });
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
  // on ingested data without a real sign-in. Never enable in production.
  if (config.devLogin) {
    router.get('/auth/dev-login', async ctx => {
      const db = await getDb();
      await db.run(
        `INSERT INTO users (id, provider, name, email, avatar, created_at) VALUES ('dev:local', 'dev', 'Local Dev', 'dev@local', NULL, ?)
         ON CONFLICT(id) DO NOTHING`,
        [new Date().toISOString()],
      );
      await createSession(ctx, 'dev:local');
      redirect(ctx, config.appUrl);
    });
  }
}
