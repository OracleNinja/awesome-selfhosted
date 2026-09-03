import { describe, expect, it } from 'vitest';
import {
  addUser,
  asAnon,
  asUser,
  freshDatabase,
  locationId,
  makeAdmin,
  seedOrg,
  signUp,
} from './harness';

describe('account provisioning', () => {
  it('puts an uninvited signup in the pending queue with no access', async () => {
    const org = await seedOrg();
    const id = await signUp(org.db, 'John@Example.com', {
      full_name: 'John Smith',
      requested_location_id: org.hib2,
    });

    const row = (
      await org.db.query<{ status: string; role: string; email: string; location_id: string }>(
        'select status, role, email, location_id from public.app_users where id = $1',
        [id],
      )
    ).rows[0]!;

    expect(row.status).toBe('pending');
    expect(row.role).toBe('employee');
    expect(row.email).toBe('john@example.com');
    expect(row.location_id).toBe(org.hib2);

    // A pending account can see itself, and the list of locations so it can
    // say where it works. Nothing else.
    await asUser(org.db, id, async (c) => {
      expect((await c.query('select * from public.app_users')).rows).toHaveLength(1);
      expect((await c.query('select * from public.locations')).rows).toHaveLength(4);
      expect((await c.query('select * from public.inventory_items')).rows).toHaveLength(0);
      expect((await c.query('select * from public.transfers')).rows).toHaveLength(0);
      expect((await c.query('select * from public.audit_log')).rows).toHaveLength(0);
    });
  });

  it('records the access request in the audit log', async () => {
    const org = await seedOrg();
    const id = await signUp(org.db, 'john@example.com', { full_name: 'John Smith' });
    const rows = (
      await org.db.query<{ action: string; new_value: { email: string } }>(
        "select action, new_value from public.audit_log" +
          " where action = 'user.access_requested' and entity_id = $1",
        [id],
      )
    ).rows;
    expect(rows).toHaveLength(1);
    expect(rows[0]!.new_value.email).toBe('john@example.com');
  });

  it('activates an invited email on signup with the invited role and location', async () => {
    const org = await seedOrg();
    await asUser(org.db, org.adminId, async (c) => {
      await c.query(
        'select public.admin_create_invitation($1, $2, $3, $4::public.user_role)',
        ['Maria@Example.com', 'Maria Lopez', org.taco, 'manager'],
      );
    });

    const id = await signUp(org.db, 'maria@example.com');
    const row = (
      await org.db.query<{ status: string; role: string; location_id: string; full_name: string }>(
        'select status, role, location_id, full_name from public.app_users where id = $1',
        [id],
      )
    ).rows[0]!;

    expect(row.status).toBe('active');
    expect(row.role).toBe('manager');
    expect(row.location_id).toBe(org.taco);
    expect(row.full_name).toBe('Maria Lopez');

    const invite = (
      await org.db.query<{ accepted_by: string }>(
        'select accepted_by from public.invitations where email = $1',
        ['maria@example.com'],
      )
    ).rows[0]!;
    expect(invite.accepted_by).toBe(id);
  });

  it('does not honour a revoked invitation', async () => {
    const org = await seedOrg();
    const inviteId = await asUser(org.db, org.adminId, async (c) => {
      const res = await c.query<{ id: string }>(
        'select public.admin_create_invitation($1, $2, $3, $4::public.user_role) as id',
        ['gone@example.com', 'Gone Away', org.taco, 'manager'],
      );
      return res.rows[0]!.id;
    });
    await asUser(org.db, org.adminId, async (c) => {
      await c.query('select public.admin_revoke_invitation($1)', [inviteId]);
    });

    const id = await signUp(org.db, 'gone@example.com');
    const row = (
      await org.db.query<{ status: string; role: string }>(
        'select status, role from public.app_users where id = $1',
        [id],
      )
    ).rows[0]!;
    expect(row.status).toBe('pending');
    expect(row.role).toBe('employee');
  });

  it('bootstraps the first admin only for an account that already signed up', async () => {
    const db = await freshDatabase();
    await expect(makeAdmin(db, 'nobody@example.com')).rejects.toThrow(/Sign up in the app first/);

    await signUp(db, 'first@example.com', { full_name: 'First Admin' });
    await makeAdmin(db, 'first@example.com');
    const row = (
      await db.query<{ role: string; status: string }>(
        'select role, status from public.app_users where email = $1',
        ['first@example.com'],
      )
    ).rows[0]!;
    expect(row.role).toBe('admin');
    expect(row.status).toBe('active');
  });

  it('never lets an ordinary user call the bootstrap function', async () => {
    const org = await seedOrg();
    const employee = await addUser(org, 'e@example.com', 'Eve', 'employee', org.hib2);
    await expect(
      asUser(org.db, employee, (c) => c.query('select public.bootstrap_admin($1)', ['e@example.com'])),
    ).rejects.toThrow(/permission denied/i);
  });

  it('signs out to nothing: the anonymous role cannot read any table', async () => {
    const org = await seedOrg();
    await asAnon(org.db, async (c) => {
      for (const table of [
        'locations',
        'app_users',
        'inventory_items',
        'transfers',
        'audit_log',
        'invitations',
      ]) {
        await expect(c.query(`select * from public.${table}`)).rejects.toThrow(/permission denied/i);
      }
    });
  });

  it('lets a pending account state where it works, once', async () => {
    const org = await seedOrg();
    const id = await signUp(org.db, 'ping@example.com', { full_name: 'Ping Wu' });
    await asUser(org.db, id, (c) =>
      c.query('select public.set_requested_location($1)', [org.hib3]),
    );
    const loc = (
      await org.db.query<{ location_id: string }>(
        'select location_id from public.app_users where id = $1',
        [id],
      )
    ).rows[0]!.location_id;
    expect(loc).toBe(org.hib3);

    // Once approved, the employee can no longer move themselves.
    await asUser(org.db, org.adminId, (c) =>
      c.query(
        'select public.admin_update_user($1, null, $2, $3::public.user_status)',
        [id, org.hib3, 'active'],
      ),
    );
    await expect(
      asUser(org.db, id, (c) => c.query('select public.set_requested_location($1)', [org.hib1])),
    ).rejects.toThrow(/administrator sets your location/i);
  });

  it('a disabled account loses access immediately', async () => {
    const org = await seedOrg();
    const employee = await addUser(org, 'bye@example.com', 'Bye', 'employee', org.hib2);
    await asUser(org.db, employee, async (c) => {
      expect((await c.query('select * from public.locations')).rows.length).toBeGreaterThan(0);
    });

    await asUser(org.db, org.adminId, async (c) => {
      await c.query('select public.admin_update_user($1, null, null, $2::public.user_status)', [
        employee,
        'disabled',
      ]);
    });

    await asUser(org.db, employee, async (c) => {
      expect((await c.query('select * from public.locations')).rows).toHaveLength(0);
      await expect(
        c.query('select public.create_transfer($1::public.transfer_kind, $2, $3::jsonb)', [
          'take',
          org.taco,
          '[]',
        ]),
      ).rejects.toThrow(/not active/i);
    });
  });

  it('tracks recent activity without any location or device detail', async () => {
    const org = await seedOrg();
    const employee = await addUser(org, 'seen@example.com', 'Seen', 'employee', org.hib2);
    await asUser(org.db, employee, (c) => c.query('select public.touch_presence()'));
    const row = (
      await org.db.query<{ last_seen_at: Date | null }>(
        'select last_seen_at from public.app_users where id = $1',
        [employee],
      )
    ).rows[0]!;
    expect(row.last_seen_at).not.toBeNull();
  });

  it('keeps at least one active administrator', async () => {
    const org = await seedOrg();
    await expect(
      asUser(org.db, org.adminId, (c) =>
        c.query('select public.admin_update_user($1, $2::public.user_role, null, null)', [
          org.adminId,
          'employee',
        ]),
      ),
    ).rejects.toThrow(/at least one active administrator/i);

    // With a second admin in place the demotion is allowed.
    await addUser(org, 'admin2@example.com', 'Second Admin', 'admin', org.hib1);
    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.admin_update_user($1, $2::public.user_role, null, null)', [
        org.adminId,
        'manager',
      ]),
    );
    const role = (
      await org.db.query<{ role: string }>('select role from public.app_users where id = $1', [
        org.adminId,
      ])
    ).rows[0]!.role;
    expect(role).toBe('manager');
  });

  it('refuses to activate an account with no location', async () => {
    const org = await seedOrg();
    const id = await signUp(org.db, 'noloc@example.com', { full_name: 'No Location' });
    await expect(
      asUser(org.db, org.adminId, (c) =>
        c.query('select public.admin_update_user($1, null, null, $2::public.user_status)', [
          id,
          'active',
        ]),
      ),
    ).rejects.toThrow(/Assign a location/i);
  });

  it('rejects an invitation for an email that already has an account', async () => {
    const org = await seedOrg();
    await addUser(org, 'taken@example.com', 'Taken', 'employee', org.hib2);
    await expect(
      asUser(org.db, org.adminId, (c) =>
        c.query('select public.admin_create_invitation($1, $2, $3, $4::public.user_role)', [
          'taken@example.com',
          'Taken Again',
          org.hib2,
          'employee',
        ]),
      ),
    ).rejects.toThrow(/already has an account/i);
  });

  it('exposes the seeded locations to any active user', async () => {
    const org = await seedOrg();
    const employee = await addUser(org, 'any@example.com', 'Any', 'employee', org.hib2);
    const names = await asUser(org.db, employee, async (c) =>
      (await c.query<{ name: string }>('select name from public.locations order by name')).rows.map(
        (r) => r.name,
      ),
    );
    expect(names).toEqual(['287 Taco Shop', 'Hibachio 1', 'Hibachio 2', 'Hibachio 3']);
    expect(await locationId(org.db, '287 Taco Shop')).toBe(org.taco);
  });
});
