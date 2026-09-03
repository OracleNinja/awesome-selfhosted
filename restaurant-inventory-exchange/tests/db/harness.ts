import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { PGlite } from '@electric-sql/pglite';

const ROOT = new URL('../../', import.meta.url).pathname;
const MIGRATIONS = join(ROOT, 'supabase', 'migrations');
const SHIM = join(ROOT, 'supabase', 'tests', '00_supabase_shim.sql');

export type Ctx = { db: PGlite };

/** Boots an empty Postgres and applies the shim plus every real migration. */
export async function freshDatabase(): Promise<PGlite> {
  const db = await PGlite.create();
  await db.exec(readFileSync(SHIM, 'utf8'));
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith('.sql')).sort()) {
    const sql = readFileSync(join(MIGRATIONS, file), 'utf8');
    try {
      await db.exec(sql);
    } catch (error) {
      throw new Error(`migration ${file} failed: ${(error as Error).message}`);
    }
  }
  return db;
}

/** Runs a callback as a signed-in Supabase user (role `authenticated`). */
export async function asUser<T>(
  db: PGlite,
  userId: string | null,
  fn: (db: PGlite) => Promise<T>,
): Promise<T> {
  const claims = userId ? JSON.stringify({ sub: userId, role: 'authenticated' }) : null;
  await db.exec('set role authenticated');
  await db.query('select set_config($1, $2, false)', ['request.jwt.claims', claims]);
  try {
    return await fn(db);
  } finally {
    await db.exec('reset role');
    await db.query('select set_config($1, $2, false)', ['request.jwt.claims', null]);
  }
}

/** Runs a callback as the anonymous (not signed in) role. */
export async function asAnon<T>(db: PGlite, fn: (db: PGlite) => Promise<T>): Promise<T> {
  await db.exec('set role anon');
  await db.query('select set_config($1, $2, false)', ['request.jwt.claims', null]);
  try {
    return await fn(db);
  } finally {
    await db.exec('reset role');
  }
}

/** Creates an auth user, which fires the provisioning trigger. */
export async function signUp(
  db: PGlite,
  email: string,
  meta: Record<string, unknown> = {},
): Promise<string> {
  const res = await db.query<{ id: string }>(
    'insert into auth.users (email, raw_user_meta_data) values ($1, $2::jsonb) returning id',
    [email.toLowerCase(), JSON.stringify(meta)],
  );
  return res.rows[0].id;
}

export async function locationId(db: PGlite, name: string): Promise<string> {
  const res = await db.query<{ id: string }>('select id from public.locations where name = $1', [
    name,
  ]);
  if (!res.rows[0]) throw new Error(`no location named ${name}`);
  return res.rows[0].id;
}

export async function itemId(db: PGlite, name: string): Promise<string> {
  const res = await db.query<{ id: string }>(
    'select id from public.inventory_items where name = $1',
    [name],
  );
  if (!res.rows[0]) throw new Error(`no item named ${name}`);
  return res.rows[0].id;
}

/** Promotes a signed-up account to an active admin, as the bootstrap docs do. */
export async function makeAdmin(db: PGlite, email: string): Promise<void> {
  await db.query('select public.bootstrap_admin($1)', [email.toLowerCase()]);
}

export async function activate(
  db: PGlite,
  adminId: string,
  userId: string,
  role: 'admin' | 'manager' | 'employee',
  location: string,
): Promise<void> {
  const loc = await locationId(db, location);
  await asUser(db, adminId, async (c) => {
    await c.query('select public.admin_update_user($1, $2::public.user_role, $3, $4::public.user_status)', [
      userId,
      role,
      loc,
      'active',
    ]);
  });
}

export type Org = {
  db: PGlite;
  adminId: string;
  hib1: string;
  hib2: string;
  hib3: string;
  taco: string;
};

/**
 * A realistic starting point: four locations from the seed migration, one
 * bootstrapped admin, and nothing else. Individual tests add the users they
 * need so each one states its own preconditions.
 */
export async function seedOrg(): Promise<Org> {
  const db = await freshDatabase();
  await signUp(db, 'owner@example.com', { full_name: 'Ada Owner' });
  await makeAdmin(db, 'owner@example.com');
  const adminId = (
    await db.query<{ id: string }>('select id from public.app_users where email = $1', [
      'owner@example.com',
    ])
  ).rows[0]!.id;
  await asUser(db, adminId, async (c) => {
    await c.query(
      'select public.admin_update_user($1, null, $2, null)',
      [adminId, await locationId(db, 'Hibachio 1')],
    );
  });
  return {
    db,
    adminId,
    hib1: await locationId(db, 'Hibachio 1'),
    hib2: await locationId(db, 'Hibachio 2'),
    hib3: await locationId(db, 'Hibachio 3'),
    taco: await locationId(db, '287 Taco Shop'),
  };
}

/** Signs a user up and has the admin approve them in one step. */
export async function addUser(
  org: Org,
  email: string,
  fullName: string,
  role: 'admin' | 'manager' | 'employee',
  location: string,
): Promise<string> {
  const id = await signUp(org.db, email, { full_name: fullName });
  await asUser(org.db, org.adminId, async (c) => {
    await c.query(
      'select public.admin_update_user($1, $2::public.user_role, $3, $4::public.user_status)',
      [id, role, location, 'active'],
    );
  });
  return id;
}

/** Convenience: record a transfer as a given user and return its id. */
export async function recordTransfer(
  db: PGlite,
  userId: string,
  kind: 'take' | 'give',
  counterparty: string,
  items: Array<{ item_id: string; quantity: number }>,
  note: string | null = null,
): Promise<string> {
  return asUser(db, userId, async (c) => {
    const res = await c.query<{ create_transfer: string }>(
      'select public.create_transfer($1::public.transfer_kind, $2, $3::jsonb, $4) as create_transfer',
      [kind, counterparty, JSON.stringify(items), note],
    );
    return res.rows[0]!.create_transfer;
  });
}
