import { describe, expect, it } from 'vitest';
import { addUser, asUser, itemId, recordTransfer, seedOrg } from './harness';

async function setup() {
  const org = await seedOrg();
  const john = await addUser(org, 'john@example.com', 'John Smith', 'employee', org.hib2);
  const cups = await itemId(org.db, '32 oz Cups');
  return { org, john, cups };
}

describe('corrections keep the original record', () => {
  it('leaves the original quantity untouched and stores both values', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 5 },
    ]);
    const lineId = (
      await org.db.query<{ id: string }>(
        'select id from public.transfer_items where transfer_id = $1',
        [id],
      )
    ).rows[0]!.id;

    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.adjust_transfer_item($1, $2, $3)', [lineId, 2, 'Counted sleeves twice']),
    );

    const original = (
      await org.db.query<{ quantity: string }>(
        'select quantity from public.transfer_items where id = $1',
        [lineId],
      )
    ).rows[0]!;
    expect(Number(original.quantity)).toBe(5);

    const view = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ original_quantity: string; effective_quantity: string; adjusted: boolean }>(
          'select * from public.transfer_line_feed where id = $1',
          [lineId],
        )
      ).rows[0]!,
    );
    expect(Number(view.original_quantity)).toBe(5);
    expect(Number(view.effective_quantity)).toBe(2);
    expect(view.adjusted).toBe(true);

    const audit = (
      await org.db.query<{
        old_value: { quantity: string };
        new_value: { quantity: string; reason: string };
        actor_id: string;
      }>("select * from public.audit_log where action = 'transfer.line_corrected'")
    ).rows[0]!;
    expect(Number(audit.old_value.quantity)).toBe(5);
    expect(Number(audit.new_value.quantity)).toBe(2);
    expect(audit.new_value.reason).toBe('Counted sleeves twice');
    expect(audit.actor_id).toBe(org.adminId);
  });

  it('chains corrections from the most recent value', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 5 },
    ]);
    const lineId = (
      await org.db.query<{ id: string }>(
        'select id from public.transfer_items where transfer_id = $1',
        [id],
      )
    ).rows[0]!.id;

    await asUser(org.db, org.adminId, async (c) => {
      await c.query('select public.adjust_transfer_item($1, $2, $3)', [lineId, 3, 'first pass']);
      await c.query('select public.adjust_transfer_item($1, $2, $3)', [lineId, 1, 'second pass']);
    });

    const adjustments = (
      await org.db.query<{ previous_quantity: string; new_quantity: string }>(
        'select previous_quantity, new_quantity from public.transfer_adjustments' +
          ' where transfer_item_id = $1 order by created_at, id',
        [lineId],
      )
    ).rows;
    expect(adjustments.map((a) => [Number(a.previous_quantity), Number(a.new_quantity)])).toEqual([
      [5, 3],
      [3, 1],
    ]);

    const effective = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ effective_quantity: string }>(
          'select effective_quantity from public.transfer_line_feed where id = $1',
          [lineId],
        )
      ).rows[0]!.effective_quantity,
    );
    expect(Number(effective)).toBe(1);
  });

  it('requires a reason for every correction', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 5 },
    ]);
    const lineId = (
      await org.db.query<{ id: string }>(
        'select id from public.transfer_items where transfer_id = $1',
        [id],
      )
    ).rows[0]!.id;
    await expect(
      asUser(org.db, org.adminId, (c) =>
        c.query('select public.adjust_transfer_item($1, $2, $3)', [lineId, 1, '   ']),
      ),
    ).rejects.toThrow(/reason is required/i);
    await expect(
      asUser(org.db, org.adminId, (c) =>
        c.query('select public.void_transfer($1, $2)', [id, null]),
      ),
    ).rejects.toThrow(/reason is required/i);
  });

  it('voids a transfer without deleting it', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 5 },
    ]);
    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.void_transfer($1, $2)', [id, 'Duplicate entry']),
    );

    const still = (
      await org.db.query('select id from public.transfers where id = $1', [id])
    ).rows;
    expect(still).toHaveLength(1);

    const feed = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ voided: boolean }>('select voided from public.transfer_feed where id = $1', [
          id,
        ])
      ).rows[0]!,
    );
    expect(feed.voided).toBe(true);

    await expect(
      asUser(org.db, org.adminId, (c) =>
        c.query('select public.void_transfer($1, $2)', [id, 'again']),
      ),
    ).rejects.toThrow(/already voided/i);
  });

  it('will not confirm a voided transfer', async () => {
    const { org, john, cups } = await setup();
    const maria = await addUser(org, 'maria@example.com', 'Maria Lopez', 'manager', org.taco);
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 5 },
    ]);
    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.void_transfer($1, $2)', [id, 'Duplicate entry']),
    );
    await expect(
      asUser(org.db, maria, (c) => c.query('select public.confirm_transfer($1, null)', [id])),
    ).rejects.toThrow(/was voided/i);
  });
});

describe('managing locations, catalog and users', () => {
  it('adds and renames a location, recording the old and new value', async () => {
    const { org } = await setup();
    const id = await asUser(org.db, org.adminId, async (c) => {
      const res = await c.query<{ id: string }>(
        'select public.admin_upsert_location($1) as id',
        ['Hibachio 4'],
      );
      return res.rows[0]!.id;
    });
    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.admin_upsert_location($1, $2, $3)', ['Hibachio Four', id, true]),
    );
    const name = (
      await org.db.query<{ name: string }>('select name from public.locations where id = $1', [id])
    ).rows[0]!.name;
    expect(name).toBe('Hibachio Four');

    const audit = (
      await org.db.query<{ old_value: { name: string }; new_value: { name: string } }>(
        "select * from public.audit_log where action = 'location.updated'",
      )
    ).rows[0]!;
    expect(audit.old_value.name).toBe('Hibachio 4');
    expect(audit.new_value.name).toBe('Hibachio Four');
  });

  it('will not record a transfer against a deactivated location', async () => {
    const { org, john, cups } = await setup();
    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.admin_upsert_location($1, $2, $3)', ['287 Taco Shop', org.taco, false]),
    );
    await expect(
      recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 1 }]),
    ).rejects.toThrow(/inactive location/i);
  });

  it('adds a catalog item that employees can immediately use', async () => {
    const { org, john } = await setup();
    const itemIdNew = await asUser(org.db, org.adminId, async (c) => {
      const res = await c.query<{ id: string }>(
        'select public.admin_upsert_item($1, $2, $3) as id',
        ['Sanitizer Buckets', 'Supplies', 'each'],
      );
      return res.rows[0]!.id;
    });
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: itemIdNew, quantity: 3 },
    ]);
    const line = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ item_name: string; unit: string }>(
          'select item_name, unit from public.transfer_line_feed where transfer_id = $1',
          [id],
        )
      ).rows[0]!,
    );
    expect(line.item_name).toBe('Sanitizer Buckets');
    expect(line.unit).toBe('each');
  });

  it('keeps a location-specific item out of unrelated transfers', async () => {
    const { org, john } = await setup();
    const localItem = await asUser(org.db, org.adminId, async (c) => {
      const res = await c.query<{ id: string }>(
        'select public.admin_upsert_item($1, $2, $3, null, null, true, null, $4) as id',
        ['Taco Press', 'Equipment', 'each', org.taco],
      );
      return res.rows[0]!.id;
    });
    // Fine between Hibachio 2 and the Taco Shop, which owns the item.
    await recordTransfer(org.db, john, 'take', org.taco, [{ item_id: localItem, quantity: 1 }]);
    // Not fine between two locations that have nothing to do with it.
    await expect(
      recordTransfer(org.db, john, 'take', org.hib1, [{ item_id: localItem, quantity: 1 }]),
    ).rejects.toThrow(/does not exist at either location/i);
  });

  it('approves a pending user and shows them in the roster', async () => {
    const { org } = await setup();
    const pendingId = (
      await org.db.query<{ id: string }>(
        `insert into auth.users (email, raw_user_meta_data)
         values ('new@example.com', jsonb_build_object('full_name', 'New Person'))
         returning id`,
      )
    ).rows[0]!.id;

    const pending = await asUser(org.db, org.adminId, async (c) =>
      (
        await c.query<{ full_name: string }>(
          "select full_name from public.app_users where status = 'pending'",
        )
      ).rows,
    );
    expect(pending.map((p) => p.full_name)).toEqual(['New Person']);

    await asUser(org.db, org.adminId, (c) =>
      c.query(
        'select public.admin_update_user($1, $2::public.user_role, $3, $4::public.user_status)',
        [pendingId, 'employee', org.hib2, 'active'],
      ),
    );

    const row = (
      await org.db.query<{ status: string; location_id: string }>(
        'select status, location_id from public.app_users where id = $1',
        [pendingId],
      )
    ).rows[0]!;
    expect(row.status).toBe('active');
    expect(row.location_id).toBe(org.hib2);

    const audit = (
      await org.db.query<{ old_value: { status: string }; new_value: { status: string } }>(
        "select * from public.audit_log where action = 'user.updated' and entity_id = $1",
        [pendingId],
      )
    ).rows[0]!;
    expect(audit.old_value.status).toBe('pending');
    expect(audit.new_value.status).toBe('active');
  });

  it('turns item pricing on only when an admin asks for it', async () => {
    const { org, john } = await setup();
    const initial = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ value: boolean }>(
          "select value from public.app_settings where key = 'pricing_enabled'",
        )
      ).rows[0]!.value,
    );
    expect(initial).toBe(false);

    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.admin_set_setting($1, $2::jsonb)', ['pricing_enabled', 'true']),
    );
    const after = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ value: boolean }>(
          "select value from public.app_settings where key = 'pricing_enabled'",
        )
      ).rows[0]!.value,
    );
    expect(after).toBe(true);
  });
});

describe('admin reporting filters', () => {
  it('answers "everything Hibachio 2 took from 287 Taco Shop this month"', async () => {
    const { org, john, cups } = await setup();
    const gloves = await itemId(org.db, 'Gloves');
    await recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 2 }]);
    await recordTransfer(org.db, john, 'take', org.taco, [{ item_id: gloves, quantity: 1 }]);
    await recordTransfer(org.db, john, 'give', org.taco, [{ item_id: cups, quantity: 4 }]);
    await recordTransfer(org.db, john, 'take', org.hib1, [{ item_id: cups, quantity: 9 }]);

    const rows = await asUser(org.db, org.adminId, async (c) =>
      (
        await c.query<{ item_name: string; effective_quantity: string }>(
          `select l.item_name, l.effective_quantity
             from public.transfer_feed t
             join public.transfer_line_feed l on l.transfer_id = t.id
            where t.from_location_id = $1
              and t.to_location_id = $2
              and t.recorded_at >= date_trunc('month', now())
            order by l.item_name`,
          [org.taco, org.hib2],
        )
      ).rows,
    );
    expect(rows.map((r) => [r.item_name, Number(r.effective_quantity)])).toEqual([
      ['32 oz Cups', 2],
      ['Gloves', 1],
    ]);
  });

  it('filters by employee and by confirmation state', async () => {
    const { org, john, cups } = await setup();
    const other = await addUser(org, 'kim@example.com', 'Kim Park', 'employee', org.hib2);
    const maria = await addUser(org, 'maria@example.com', 'Maria Lopez', 'manager', org.taco);
    const a = await recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 2 }]);
    await recordTransfer(org.db, other, 'take', org.taco, [{ item_id: cups, quantity: 3 }]);
    await asUser(org.db, maria, (c) => c.query('select public.confirm_transfer($1, null)', [a]));

    const byEmployee = await asUser(org.db, org.adminId, async (c) =>
      (
        await c.query<{ id: string }>('select id from public.transfer_feed where recorded_by = $1', [
          john,
        ])
      ).rows,
    );
    expect(byEmployee.map((r) => r.id)).toEqual([a]);

    const unconfirmed = await asUser(org.db, org.adminId, async (c) =>
      (await c.query<{ id: string }>('select id from public.transfer_feed where not confirmed')).rows,
    );
    expect(unconfirmed).toHaveLength(1);
    expect(unconfirmed[0]!.id).not.toBe(a);
  });
});
