-- Read models. Views that read business data use security_invoker so the
-- caller's RLS policies still apply. The one exception is user_directory,
-- which deliberately runs as its owner in order to expose *only* a name and
-- location for every active user, so history can say "recorded by John"
-- without opening up the app_users table.

create view public.user_directory as
  select u.id, u.full_name, u.location_id
  from public.app_users u
  where u.status <> 'pending'
    and public.is_active_user();

grant select on public.user_directory to authenticated;

-- Effective quantities: original quantity, overridden by the most recent
-- quantity adjustment, forced to zero when the whole transfer was voided.
create view public.transfer_items_effective
with (security_invoker = on) as
  select
    ti.id,
    ti.transfer_id,
    ti.item_id,
    ti.unit,
    ti.quantity as original_quantity,
    case
      when v.transfer_id is not null then 0::numeric
      else coalesce(latest.new_quantity, ti.quantity)
    end as effective_quantity,
    (v.transfer_id is not null) as voided,
    (latest.new_quantity is not null) as adjusted
  from public.transfer_items ti
  left join lateral (
    select a.new_quantity
    from public.transfer_adjustments a
    where a.transfer_item_id = ti.id and a.kind = 'quantity'
    order by a.created_at desc, a.id desc
    limit 1
  ) latest on true
  left join (
    select distinct transfer_id
    from public.transfer_adjustments
    where kind = 'void'
  ) v on v.transfer_id = ti.transfer_id;

grant select on public.transfer_items_effective to authenticated;

-- One row per transfer, with names resolved and confirmation state attached.
create view public.transfer_feed
with (security_invoker = on) as
  select
    t.id,
    t.kind,
    t.from_location_id,
    fl.name as from_location_name,
    t.to_location_id,
    tl.name as to_location_name,
    t.recorded_by,
    rec.full_name as recorded_by_name,
    t.recorded_by_location_id,
    t.recorded_at,
    t.note,
    t.confirming_location_id,
    c.confirmed_at,
    c.confirmed_by,
    conf.full_name as confirmed_by_name,
    (c.id is not null) as confirmed,
    exists (
      select 1 from public.transfer_adjustments a
      where a.transfer_id = t.id and a.kind = 'void'
    ) as voided
  from public.transfers t
  join public.locations fl on fl.id = t.from_location_id
  join public.locations tl on tl.id = t.to_location_id
  left join public.user_directory rec on rec.id = t.recorded_by
  left join public.transfer_confirmations c on c.transfer_id = t.id
  left join public.user_directory conf on conf.id = c.confirmed_by;

grant select on public.transfer_feed to authenticated;

-- One row per line item on a transfer, ready to render.
create view public.transfer_line_feed
with (security_invoker = on) as
  select
    tie.id,
    tie.transfer_id,
    tie.item_id,
    i.name as item_name,
    i.category as item_category,
    tie.unit,
    tie.original_quantity,
    tie.effective_quantity,
    tie.adjusted,
    tie.voided
  from public.transfer_items_effective tie
  join public.inventory_items i on i.id = tie.item_id;

grant select on public.transfer_line_feed to authenticated;

-- Net position per location pair, per item. Positive net_quantity means
-- location_a has handed location_b that many units more than it received.
create view public.location_item_balances
with (security_invoker = on) as
  with lines as (
    select
      t.from_location_id,
      t.to_location_id,
      tie.item_id,
      tie.unit,
      tie.effective_quantity as quantity
    from public.transfers t
    join public.transfer_items_effective tie on tie.transfer_id = t.id
    where not tie.voided
  ),
  directional as (
    select from_location_id as location_a, to_location_id as location_b,
           item_id, unit, quantity from lines
    union all
    select to_location_id, from_location_id,
           item_id, unit, -quantity from lines
  )
  select
    d.location_a,
    la.name as location_a_name,
    d.location_b,
    lb.name as location_b_name,
    d.item_id,
    i.name as item_name,
    d.unit,
    sum(d.quantity) as net_quantity
  from directional d
  join public.locations la on la.id = d.location_a
  join public.locations lb on lb.id = d.location_b
  join public.inventory_items i on i.id = d.item_id
  group by d.location_a, la.name, d.location_b, lb.name, d.item_id, i.name, d.unit
  having sum(d.quantity) <> 0;

grant select on public.location_item_balances to authenticated;
