/**
 * Google OAuth 2.0 client (no SDK dependency — the API is plain HTTPS).
 * Docs: https://developers.google.com/identity/protocols/oauth2/web-server
 *
 * Standard authorization-code flow for a confidential client: we hold the
 * client secret server-side, so no PKCE is needed — CSRF is covered by the
 * `state` parameter, which the caller generates and verifies.
 *
 * Identity comes from the userinfo endpoint rather than by decoding the
 * id_token: the code is exchanged over TLS directly with Google, so one extra
 * round trip buys us the same guarantee without hand-rolling JWT verification.
 */
import { config } from './config.ts';

const AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth';
const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo';

export interface GoogleProfile {
  /** Stable Google account id — never reused, unlike email. */
  sub: string;
  name: string;
  email: string;
  email_verified: boolean;
  picture: string | null;
}

export class GoogleError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'GoogleError';
    this.status = status;
  }
}

async function googleFetch<T>(url: string, opts: {
  method?: string;
  accessToken?: string;
  form?: Record<string, string>;
} = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.accessToken) headers['Authorization'] = `Bearer ${opts.accessToken}`;
  let body: string | undefined;
  if (opts.form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    body = new URLSearchParams(opts.form).toString();
  }
  const res = await fetch(url, {
    method: opts.method ?? 'GET',
    headers,
    body,
    signal: AbortSignal.timeout(15000),
  });
  const text = await res.text();
  if (!res.ok) {
    // Google returns {error, error_description}; fall back to the raw body.
    let message = text;
    try {
      const parsed = JSON.parse(text) as { error?: string; error_description?: string };
      message = parsed.error_description ?? parsed.error ?? text;
    } catch { /* non-JSON error body */ }
    throw new GoogleError(res.status, message);
  }
  return JSON.parse(text) as T;
}

/** Where to send the browser to start the flow. `state` guards against CSRF. */
export function loginUrl(state: string): string {
  const params = new URLSearchParams({
    client_id: config.googleClientId,
    redirect_uri: config.googleRedirectUrl,
    response_type: 'code',
    scope: 'openid email profile',
    state,
    // Always let the user pick an account rather than silently reusing the
    // one Google happens to have signed in — this is a shared-machine tool.
    prompt: 'select_account',
  });
  return `${AUTH_URL}?${params.toString()}`;
}

export async function exchangeCode(code: string): Promise<{ access_token: string }> {
  return googleFetch<{ access_token: string }>(TOKEN_URL, {
    method: 'POST',
    form: {
      code,
      client_id: config.googleClientId,
      client_secret: config.googleClientSecret,
      redirect_uri: config.googleRedirectUrl,
      grant_type: 'authorization_code',
    },
  });
}

export async function getProfile(accessToken: string): Promise<GoogleProfile> {
  return googleFetch<GoogleProfile>(USERINFO_URL, { accessToken });
}
