import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { PGlite } from '@electric-sql/pglite';
import { BUNDLE_PATH, buildBundle } from '../../scripts/build-bootstrap-sql.mjs';

/**
 * supabase/bootstrap.sql is what a new deployment actually pastes into the
 * Supabase SQL editor. These tests keep it honest: it has to match the
 * migrations it is generated from, and it has to produce a working database
 * on its own.
 */
describe('the one-paste setup bundle', () => {
  it('matches the migrations it is generated from', () => {
    const onDisk = readFileSync(BUNDLE_PATH, 'utf8');
    expect(onDisk).toBe(buildBundle());
  });

  it('builds a complete database in a single run', async () => {
    const db = await PGlite.create();
    await db.exec(readFileSync('supabase/tests/00_supabase_shim.sql', 'utf8'));
    await db.exec(readFileSync(BUNDLE_PATH, 'utf8'));

    const locations = (
      await db.query<{ name: string }>('select name from public.locations order by name')
    ).rows.map((row) => row.name);
    expect(locations).toEqual(['287 Taco Shop', 'Hibachio 1', 'Hibachio 2', 'Hibachio 3']);

    const items = (
      await db.query<{ count: number }>('select count(*)::int as count from public.inventory_items')
    ).rows[0]!.count;
    expect(items).toBeGreaterThan(8);

    // The pieces the app depends on all exist.
    for (const fn of [
      'create_transfer',
      'confirm_transfer',
      'adjust_transfer_item',
      'void_transfer',
      'admin_update_user',
      'admin_create_invitation',
      'bootstrap_admin',
      'set_requested_location',
    ]) {
      const found = (
        await db.query<{ count: number }>(
          "select count(*)::int as count from pg_proc p join pg_namespace n on n.oid = p.pronamespace" +
            ' where n.nspname = $1 and p.proname = $2',
          ['public', fn],
        )
      ).rows[0]!.count;
      expect(found, `function ${fn} is missing from the bundle`).toBeGreaterThan(0);
    }

    // Row level security is on for every table the app stores data in.
    const unprotected = (
      await db.query<{ relname: string }>(
        `select c.relname from pg_class c
           join pg_namespace n on n.oid = c.relnamespace
          where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity`,
      )
    ).rows.map((row) => row.relname);
    expect(unprotected).toEqual([]);
  });
});
