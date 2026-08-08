/**
 * Storage adapter: PostgreSQL only.
 *
 * The schema lives in supabase/migrations and nowhere else — this file issues
 * no DDL. Local development runs the Supabase stack in Docker (`supabase
 * start`), which applies those same migrations, so dev and production are the
 * same database built from the same two files.
 *
 * SQL is written with ? placeholders and translated to $n here, a holdover from
 * the SQLite era that is cheap to keep and keeps the call sites terse.
 */
import { config } from './config.ts';

export interface Db {
  all<T = Record<string, unknown>>(sql: string, params?: unknown[]): Promise<T[]>;
  run(sql: string, params?: unknown[]): Promise<void>;
  exec(sql: string): Promise<void>;
}

class PgDb implements Db {
  private pool: { query: (sql: string, params?: unknown[]) => Promise<{ rows: unknown[] }> };
  constructor(pool: PgDb['pool']) { this.pool = pool; }
  static async connect(url: string): Promise<PgDb> {
    const { default: pg } = await import('pg');
    /*
     * Pool per *instance*, and on Vercel there are many instances. A default
     * pool (max 10) times a few dozen warm functions exhausts Supabase long
     * before the traffic justifies it, so the cap is small and idle sockets are
     * dropped quickly. Concurrency is meant to come from more instances, not
     * from a deep pool inside one; the connection string should point at
     * Supabase's transaction pooler (port 6543) for the same reason.
     */
    return new PgDb(new pg.Pool({
      connectionString: url,
      max: config.dbPoolMax,
      idleTimeoutMillis: 10_000,
      connectionTimeoutMillis: 10_000,
    }));
  }
  private translate(sql: string): string {
    let n = 0;
    return sql.replace(/\?/g, () => `$${++n}`);
  }
  async all<T>(sql: string, params: unknown[] = []): Promise<T[]> {
    return (await this.pool.query(this.translate(sql), params)).rows as T[];
  }
  async run(sql: string, params: unknown[] = []): Promise<void> {
    await this.pool.query(this.translate(sql), params);
  }
  async exec(sql: string): Promise<void> {
    await this.pool.query(sql);
  }
}

let dbPromise: Promise<Db> | null = null;

export function getDb(): Promise<Db> {
  if (!dbPromise) {
    dbPromise = (async () => {
      if (!config.databaseUrl) {
        // Hard failure rather than a fallback. The old SQLite default meant a
        // misconfigured deployment came up healthy and served an empty
        // database instead of refusing to start.
        throw new Error(
          'SUPABASE_DB_URL (or DATABASE_URL) is required. For local development run '
          + '`supabase start` and use the connection string it prints.',
        );
      }
      const db = await PgDb.connect(config.databaseUrl);
      console.log('[db] connected to Postgres (schema owned by supabase/migrations)');
      return db;
    })();
  }
  return dbPromise;
}

export async function metaGet(key: string): Promise<string | null> {
  const db = await getDb();
  const rows = await db.all<{ value: string }>('SELECT value FROM meta WHERE key = ?', [key]);
  return rows[0]?.value ?? null;
}

export async function metaSet(key: string, value: string): Promise<void> {
  const db = await getDb();
  await db.run(
    'INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
    [key, value],
  );
}

export async function logIngest(job: string, date: string, status: string, detail = ''): Promise<void> {
  const db = await getDb();
  await db.run('INSERT INTO ingest_log (ts, job, date, status, detail) VALUES (?, ?, ?, ?, ?)', [
    new Date().toISOString(), job, date, status, detail,
  ]);
}
