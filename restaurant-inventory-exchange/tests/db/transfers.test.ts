import { describe, expect, it } from 'vitest';
import { addUser, asUser, itemId, recordTransfer, seedOrg } from './harness';

async function setup() {
  const org = await seedOrg();
  const john = await addUser(org, 'john@example.com', 'John Smith', 'employee', org.hib2);
  const maria = await addUser(org, 'maria@example.com', 'Maria Lopez', 'manager', org.taco);
  const cups = await itemId(org.db, '32 oz Cups');
  const gloves = await itemId(org.db, 'Gloves');
  const napkins = await itemId(org.db, 'Napkins');
  return { org, john, maria, cups, gloves, napkins };
}

describe('recording transfers', () => {
  it('records a take as counterparty -> my location', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);

    // Read it back the way the app does: as the signed-in employee.
    const row = await asUser(org.db, john, async (c) =>
      (
        await c.query<{
          from_location_name: string;
          to_location_name: string;
          recorded_by_name: string;
          confirming_location_id: string;
          confirmed: boolean;
          recorded_at: Date;
        }>('select * from public.transfer_feed where id = $1', [id])
      ).rows[0]!,
    );

    expect(row.from_location_name).toBe('287 Taco Shop');
    expect(row.to_location_name).toBe('Hibachio 2');
    expect(row.recorded_by_name).toBe('John Smith');
    expect(row.confirmed).toBe(false);
    // A take is acknowledged by the location the goods came from.
    expect(row.confirming_location_id).toBe(org.taco);
    expect(row.recorded_at).toBeInstanceOf(Date);

    const line = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ item_name: string; effective_quantity: string; unit: string }>(
          'select * from public.transfer_line_feed where transfer_id = $1',
          [id],
        )
      ).rows[0]!,
    );
    expect(line.item_name).toBe('32 oz Cups');
    expect(Number(line.effective_quantity)).toBe(2);
    expect(line.unit).toBe('sleeve');
  });

  it('records a give as my location -> counterparty, confirmed by the receiver', async () => {
    const { org, maria, napkins } = await setup();
    const id = await recordTransfer(org.db, maria, 'give', org.hib2, [
      { item_id: napkins, quantity: 1 },
    ]);
    const row = (
      await org.db.query<{
        from_location_id: string;
        to_location_id: string;
        confirming_location_id: string;
      }>('select * from public.transfer_feed where id = $1', [id])
    ).rows[0]!;
    expect(row.from_location_id).toBe(org.taco);
    expect(row.to_location_id).toBe(org.hib2);
    expect(row.confirming_location_id).toBe(org.hib2);
  });

  it('supports several items in one transfer and sums duplicate lines', async () => {
    const { org, john, cups, gloves } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
      { item_id: gloves, quantity: 1 },
      { item_id: cups, quantity: 3 },
    ]);
    const lines = (
      await org.db.query<{ item_name: string; effective_quantity: string }>(
        'select item_name, effective_quantity from public.transfer_line_feed' +
          ' where transfer_id = $1 order by item_name',
        [id],
      )
    ).rows;
    expect(lines.map((l) => [l.item_name, Number(l.effective_quantity)])).toEqual([
      ['32 oz Cups', 5],
      ['Gloves', 1],
    ]);
  });

  it('rejects a transfer with no items, a bad quantity, or the same location twice', async () => {
    const { org, john, cups } = await setup();
    await expect(
      recordTransfer(org.db, john, 'take', org.taco, []),
    ).rejects.toThrow(/at least one item/i);
    await expect(
      recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 0 }]),
    ).rejects.toThrow(/greater than zero/i);
    await expect(
      recordTransfer(org.db, john, 'take', org.hib2, [{ item_id: cups, quantity: 1 }]),
    ).rejects.toThrow(/different location/i);
  });

  it('rejects an inactive catalog item', async () => {
    const { org, john, cups } = await setup();
    await asUser(org.db, org.adminId, (c) =>
      c.query(
        'select public.admin_upsert_item($1, $2, $3, $4, null, $5, null, null)',
        ['32 oz Cups', 'Cups', 'sleeve', cups, false],
      ),
    );
    await expect(
      recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 1 }]),
    ).rejects.toThrow(/inactive item/i);
  });

  it('writes an audit row naming the user, the location and the time', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);
    const row = (
      await org.db.query<{
        actor_id: string;
        actor_location_id: string;
        action: string;
        created_at: Date;
        new_value: { kind: string; lines: number };
      }>('select * from public.audit_log where entity_id = $1 and action = $2', [
        id,
        'transfer.recorded',
      ])
    ).rows[0]!;
    expect(row.actor_id).toBe(john);
    expect(row.actor_location_id).toBe(org.hib2);
    expect(row.new_value.kind).toBe('take');
    expect(row.new_value.lines).toBe(1);
    expect(row.created_at).toBeInstanceOf(Date);
  });
});

describe('confirming transfers', () => {
  it('lets the counterparty confirm and records who and when', async () => {
    const { org, maria, john, napkins } = await setup();
    const id = await recordTransfer(org.db, maria, 'give', org.hib2, [
      { item_id: napkins, quantity: 1 },
    ]);

    // John is at the receiving location, so this one is his to confirm.
    const pending = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ id: string }>(
          'select id from public.transfer_feed' +
            ' where confirming_location_id = public.my_location() and not confirmed',
        )
      ).rows,
    );
    expect(pending.map((p) => p.id)).toEqual([id]);

    await asUser(org.db, john, (c) => c.query('select public.confirm_transfer($1, $2)', [id, 'ok']));

    const row = await asUser(org.db, john, async (c) =>
      (
        await c.query<{ confirmed: boolean; confirmed_by_name: string; confirmed_at: Date }>(
          'select * from public.transfer_feed where id = $1',
          [id],
        )
      ).rows[0]!,
    );
    expect(row.confirmed).toBe(true);
    expect(row.confirmed_by_name).toBe('John Smith');
    expect(row.confirmed_at).toBeInstanceOf(Date);
  });

  it('refuses confirmation from the location that recorded it', async () => {
    const { org, maria, napkins } = await setup();
    const id = await recordTransfer(org.db, maria, 'give', org.hib2, [
      { item_id: napkins, quantity: 1 },
    ]);
    await expect(
      asUser(org.db, maria, (c) => c.query('select public.confirm_transfer($1, null)', [id])),
    ).rejects.toThrow(/only the other location/i);
  });

  it('refuses confirmation from an unrelated location', async () => {
    const { org, maria, napkins } = await setup();
    const outsider = await addUser(org, 'out@example.com', 'Out Sider', 'manager', org.hib3);
    const id = await recordTransfer(org.db, maria, 'give', org.hib2, [
      { item_id: napkins, quantity: 1 },
    ]);
    await expect(
      asUser(org.db, outsider, (c) => c.query('select public.confirm_transfer($1, null)', [id])),
    ).rejects.toThrow(/transfer not found|only the other location/i);
  });

  it('cannot be confirmed twice', async () => {
    const { org, maria, john, napkins } = await setup();
    const id = await recordTransfer(org.db, maria, 'give', org.hib2, [
      { item_id: napkins, quantity: 1 },
    ]);
    await asUser(org.db, john, (c) => c.query('select public.confirm_transfer($1, null)', [id]));
    await expect(
      asUser(org.db, john, (c) => c.query('select public.confirm_transfer($1, null)', [id])),
    ).rejects.toThrow(/already confirmed/i);
  });

  it('a take is acknowledged by the source location', async () => {
    const { org, john, maria, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);
    await expect(
      asUser(org.db, john, (c) => c.query('select public.confirm_transfer($1, null)', [id])),
    ).rejects.toThrow(/only the other location/i);
    await asUser(org.db, maria, (c) => c.query('select public.confirm_transfer($1, null)', [id]));
    const confirmed = (
      await org.db.query<{ confirmed: boolean }>(
        'select confirmed from public.transfer_feed where id = $1',
        [id],
      )
    ).rows[0]!.confirmed;
    expect(confirmed).toBe(true);
  });
});

describe('balances between locations', () => {
  it('nets what each location has handed the other', async () => {
    const { org, john, maria, cups, gloves, napkins } = await setup();
    // 287 Taco Shop gave Hibachio 2: 2 sleeves of cups and 1 box of gloves.
    await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
      { item_id: gloves, quantity: 1 },
    ]);
    // Hibachio 2 gave 287 Taco Shop: 1 case of napkins.
    await recordTransfer(org.db, maria, 'take', org.hib2, [{ item_id: napkins, quantity: 1 }]);

    const rows = (
      await org.db.query<{ item_name: string; net_quantity: string; unit: string }>(
        'select item_name, net_quantity, unit from public.location_item_balances' +
          ' where location_a = $1 and location_b = $2 order by item_name',
        [org.taco, org.hib2],
      )
    ).rows;

    expect(rows.map((r) => [r.item_name, Number(r.net_quantity), r.unit])).toEqual([
      ['32 oz Cups', 2, 'sleeve'],
      ['Gloves', 1, 'box'],
      ['Napkins', -1, 'case'],
    ]);
  });

  it('cancels out when the same item goes back the other way', async () => {
    const { org, john, maria, cups } = await setup();
    await recordTransfer(org.db, john, 'take', org.taco, [{ item_id: cups, quantity: 2 }]);
    await recordTransfer(org.db, maria, 'take', org.hib2, [{ item_id: cups, quantity: 2 }]);
    const rows = (
      await org.db.query('select * from public.location_item_balances where location_a = $1', [
        org.taco,
      ])
    ).rows;
    expect(rows).toHaveLength(0);
  });

  it('leaves voided transfers out of the balance', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 2 },
    ]);
    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.void_transfer($1, $2)', [id, 'Recorded twice by mistake']),
    );
    const rows = (
      await org.db.query('select * from public.location_item_balances where location_a = $1', [
        org.taco,
      ])
    ).rows;
    expect(rows).toHaveLength(0);
  });

  it('follows quantity corrections', async () => {
    const { org, john, cups } = await setup();
    const id = await recordTransfer(org.db, john, 'take', org.taco, [
      { item_id: cups, quantity: 5 },
    ]);
    const lineId = (
      await org.db.query<{ id: string }>('select id from public.transfer_items where transfer_id = $1', [
        id,
      ])
    ).rows[0]!.id;

    await asUser(org.db, org.adminId, (c) =>
      c.query('select public.adjust_transfer_item($1, $2, $3)', [lineId, 2, 'Miscounted']),
    );

    const net = (
      await org.db.query<{ net_quantity: string }>(
        'select net_quantity from public.location_item_balances' +
          ' where location_a = $1 and location_b = $2',
        [org.taco, org.hib2],
      )
    ).rows[0]!;
    expect(Number(net.net_quantity)).toBe(2);
  });
});
