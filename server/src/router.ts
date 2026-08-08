/** Minimal zero-dependency HTTP router with JSON + cookie helpers. */
import type { IncomingMessage, ServerResponse } from 'node:http';

export interface Ctx {
  req: IncomingMessage;
  res: ServerResponse;
  url: URL;
  params: Record<string, string>;
  cookies: Record<string, string>;
  /** Set by auth middleware when a valid session exists. */
  session?: { sid: string; userId: string };
}

type Handler = (ctx: Ctx) => Promise<void> | void;

interface Route { method: string; parts: string[]; handler: Handler }

export class Router {
  private routes: Route[] = [];

  on(method: string, path: string, handler: Handler) {
    this.routes.push({ method, parts: path.split('/').filter(Boolean), handler });
  }
  get(path: string, handler: Handler) { this.on('GET', path, handler); }
  post(path: string, handler: Handler) { this.on('POST', path, handler); }
  del(path: string, handler: Handler) { this.on('DELETE', path, handler); }

  async dispatch(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? '/', 'http://localhost');
    const parts = url.pathname.split('/').filter(Boolean);
    for (const r of this.routes) {
      if (r.method !== req.method || r.parts.length !== parts.length) continue;
      const params: Record<string, string> = {};
      let ok = true;
      for (let i = 0; i < parts.length; i++) {
        if (r.parts[i].startsWith(':')) params[r.parts[i].slice(1)] = decodeURIComponent(parts[i]);
        else if (r.parts[i] !== parts[i]) { ok = false; break; }
      }
      if (!ok) continue;
      const ctx: Ctx = { req, res, url, params, cookies: parseCookies(req.headers.cookie ?? '') };
      try {
        await r.handler(ctx);
      } catch (err) {
        console.error(`[http] ${req.method} ${url.pathname} failed:`, err);
        if (!res.headersSent) json(ctx, 500, { error: 'internal_error' });
      }
      return;
    }
    json({ res } as Ctx, 404, { error: 'not_found' });
  }
}

function parseCookies(header: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq > 0) out[part.slice(0, eq).trim()] = decodeURIComponent(part.slice(eq + 1).trim());
  }
  return out;
}

/**
 * Read a JSON request body. Capped so an oversized upload cannot be buffered
 * into memory; returns null on anything unparseable.
 */
export async function readJson<T>(ctx: Ctx, maxBytes = 64 * 1024): Promise<T | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of ctx.req) {
    size += (chunk as Buffer).length;
    if (size > maxBytes) return null;
    chunks.push(chunk as Buffer);
  }
  if (!chunks.length) return null;
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8')) as T;
  } catch {
    return null;
  }
}

export function json(ctx: Ctx, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  ctx.res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  ctx.res.end(data);
}

export function redirect(ctx: Ctx, location: string): void {
  ctx.res.writeHead(302, { Location: location });
  ctx.res.end();
}

export function setCookie(ctx: Ctx, name: string, value: string, maxAgeSec: number): void {
  const cookie = `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAgeSec}`;
  const prev = ctx.res.getHeader('Set-Cookie');
  const all = prev ? ([] as string[]).concat(prev as string[], cookie) : [cookie];
  ctx.res.setHeader('Set-Cookie', all);
}
