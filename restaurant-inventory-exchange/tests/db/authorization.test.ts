import { describe, expect, it } from 'vitest';
import { addUser, asUser, itemId, recordTransfer, seedOrg } from './harness';

async function setup() {
  const org = await seedOrg();
  const john = await addUser(org, 'john@example.com', 'John Smith', 'employee', org.hib2);
  const maria = await addUser(org, 'maria@example.com', 'Maria Lopez', 'manager', org.taco);
  const outsider = await addUser(org, 'sam@example.com', 'Sam Outside', 'employee', org.hib3);
  const cups = await itemId(org.db, '32 oz Cups');
  return { org, john, maria, outsider, cups };
}

describe('what each role can see', () => {
  it('an employee sees only transfers touching their own location', async () => {
    const { org, john, maria, outsider, cups } = await setup();
    const mine = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);
    const theirs = await recordTransfer(org.db, maria, 'give', org.hib1, [
      { item_id: cups, quantity: 1 },
    ]);

    const visible = await asUser(org.db, john, async (c) =>
      (await c.query<{ id: string }>('select id from public.transfers')).rows.map((r) => r.id),
    );
    expect(visible).toContain(mine);
    expect(visible).not.toContain(theirs);

    // Someone at a third location sees neither.
    const none = await asUser(org.db, outsider, async (c) =>
      (await c.query('select id from public.transfers')).rows,
    );
    expect(none).toHaveLength(0);
  });

  it('hides the line items of a transfer the user cannot see', async () => {
    const { org, maria, outsider, cups } = await setup();
    await recordTransfer(org.db, maria, 'give', org.hib1, [{ item_id: cups, quantity: 1 }]);
    const rows = await asUser(org.db, outsider, async (c) =>
      (await c.query('select * from public.transfer_items')).rows,
    );
    expect(rows).toHaveLength(0);
  });

  it('an admin sees every transfer', async () => {
    const { org, john, maria, cups } = await setup();
    await recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 2 }]);
    await recordTransfer(org.db, maria, 'give', org.hib1, [{ item_id: cups, quantity: 1 }]);
    const rows = await asUser(org.db, org.adminId, async (c) =>
      (await c.query('select id from public.transfers')).rows,
    );
    expect(rows).toHaveLength(2);
  });

  it('an employee cannot read the user roster or the audit log', async () => {
    const { org, john } = await setup();
    await asUser(org.db, john, async (c) => {
      const users = (await c.query<{ id: string }>('select id from public.app_users')).rows;
      expect(users.map((u) => u.id)).toEqual([john]);
      expect((await c.query('select * from public.audit_log')).rows).toHaveLength(0);
      expect((await c.query('select * from public.invitations')).rows).toHaveLength(0);
    });
  });

  it('a manager sees the roster for their own location only', async () => {
    const { org, maria, john } = await setup();
    const colleague = await addUser(org, 'lu@example.com', 'Lu Ramos', 'employee', org.taco);
    const seen = await asUser(org.db, maria, async (c) =>
      (await c.query<{ id: string }>('select id from public.app_users')).rows.map((r) => r.id),
    );
    expect(seen.sort()).toEqual([maria, colleague].sort());
    expect(seen).not.toContain(john);
  });

  it('still shows who recorded a transfer without exposing their account', async () => {
    const { org, maria, john, cups } = await setup();
    const id = await recordTransfer(org.db, maria, 'give', org.hib2, [
      { item_id: cups, quantity: 1 },
    ]);
    await asUser(org.db, john, async (c) => {
      const feed = (
        await c.query<{ recorded_by_name: string }>(
          'select recorded_by_name from public.transfer_feed where id = $1',
          [id],
        )
      ).rows[0]!;
      expect(feed.recorded_by_name).toBe('Maria Lopez');
      // ...but Maria's account row itself is still off limits.
      const rows = (
        await c.query('select * from public.app_users where id = $1', [maria])
      ).rows;
      expect(rows).toHaveLength(0);
    });
  });
});

describe('writes cannot bypass the RPCs', () => {
  it('refuses direct inserts, updates and deletes on every table', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);

    await asUser(org.db, john, async (c) => {
      await expect(
        c.query(
          'insert into public.transfers (kind, from_location_id, to_location_id,' +
            ' recorded_by, recorded_by_location_id) values ($1, $2, $3, $4, $5)',
          ['take', org.taco, org.hib2, john, org.hib2],
        ),
      ).rejects.toThrow(/permission denied/i);

      await expect(
        c.query('update public.transfers set note = $1 where id = $2', ['tampered', id]),
      ).rejects.toThrow(/permission denied/i);

      await expect(c.query('delete from public.transfers where id = $1', [id])).rejects.toThrow(
        /permission denied/i,
      );

      await expect(
        c.query('update public.transfer_items set quantity = 99 where transfer_id = $1', [id]),
      ).rejects.toThrow(/permission denied/i);

      await expect(
        c.query('update public.app_users set role = $1 where id = $2', ['admin', john]),
      ).rejects.toThrow(/permission denied/i);

      await expect(
        c.query('insert into public.locations (name) values ($1)', ['Fake Shop']),
      ).rejects.toThrow(/permission denied/i);

      await expect(
        c.query('insert into public.audit_log (action, entity_type) values ($1, $2)', ['x', 'y']),
      ).rejects.toThrow(/permission denied/i);
    });
  });

  it('refuses admin RPCs to a manager and to an employee', async () => {
    const { org, john, maria } = await setup();
    for (const user of [john, maria]) {
      await asUser(org.db, user, async (c) => {
        await expect(
          c.query('select public.admin_upsert_location($1)', ['Rogue Shop']),
        ).rejects.toThrow(/Administrator access required/i);
        await expect(
          c.query('select public.admin_update_user($1, $2::public.user_role, null, null)', [
            user,
            'admin',
          ]),
        ).rejects.toThrow(/Administrator access required/i);
        await expect(
          c.query('select public.admin_create_invitation($1, $2, $3, null)', [
            'x@example.com',
            'X',
            org.hib2,
          ]),
        ).rejects.toThrow(/Administrator access required/i);
        await expect(
          c.query('select public.admin_upsert_item($1, $2, $3)', ['Rogue Item', 'Cups', 'sleeve']),
        ).rejects.toThrow(/Administrator access required/i);
        await expect(
          c.query('select public.admin_set_setting($1, $2::jsonb)', ['pricing_enabled', 'true']),
        ).rejects.toThrow(/Administrator access required/i);
      });
    }
  });

  it('refuses corrections to anyone but an admin', async () => {
    const { org, john, maria, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);
    const lineId = (
      await org.db.query<{ id: string }>(
        'select id from public.transfer_items where transfer_id = $1',
        [id],
      )
    ).rows[0]!.id;

    await expect(
      asUser(org.db, maria, (c) =>
        c.query('select public.adjust_transfer_item($1, $2, $3)', [lineId, 1, 'nope']),
      ),
    ).rejects.toThrow(/Administrator access required/i);
    await expect(
      asUser(org.db, john, (c) => c.query('select public.void_transfer($1, $2)', [id, 'nope'])),
    ).rejects.toThrow(/Administrator access required/i);
  });
});

describe('location restrictions on recording', () => {
  it('stops a non-admin recording on behalf of another location', async () => {
    const { org, john, cups } = await setup();
    await expect(
      asUser(org.db, john, (c) =>
        c.query(
          'select public.create_transfer($1::public.transfer_kind, $2, $3::jsonb, null, $4)',
          ['take', org.hib1, JSON.stringify([{ item_id: cups, quantity: 1 }]), org.taco],
        ),
      ),
    ).rejects.toThrow(/only record transfers for your own location/i);
  });

  it('lets an admin record on behalf of any location', async () => {
    const { org, cups } = await setup();
    const id = await asUser(org.db, org.adminId, async (c) => {
      const res = await c.query<{ id: string }>(
        'select public.create_transfer($1::public.transfer_kind, $2, $3::jsonb, null, $4) as id',
        ['take', org.hib1, JSON.stringify([{ item_id: cups, quantity: 1 }]), org.taco],
      );
      return res.rows[0]!.id;
    });
    const row = (
      await org.db.query<{ from_location_id: string; to_location_id: string }>(
        'select from_location_id, to_location_id from public.transfers where id = $1',
        [id],
      )
    ).rows[0]!;
    expect(row.from_location_id).toBe(org.hib1);
    expect(row.to_location_id).toBe(org.taco);
  });

  it('a pending user cannot record anything', async () => {
    const { org, cups } = await setup();
    const pendingId = (
      await org.db.query<{ id: string }>(
        "insert into auth.users (email) values ('new@example.com') returning id",
      )
    ).rows[0]!.id;
    await expect(
      asUser(org.db, pendingId, (c) =>
        c.query('select public.create_transfer($1::public.transfer_kind, $2, $3::jsonb)', [
          'take',
          org.taco,
          JSON.stringify([{ item_id: cups, quantity: 1 }]),
        ]),
      ),
    ).rejects.toThrow(/not active/i);
  });
});
