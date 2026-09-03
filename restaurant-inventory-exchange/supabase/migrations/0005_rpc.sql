-- Business operations. Every write in the application goes through one of
-- these. They are SECURITY DEFINER (so they can write to tables that have no
-- INSERT policy) and each one re-checks the caller's role and location before
-- doing anything.

-- ------------------------------------------------- account provisioning ----
-- Runs when Supabase Auth creates a user. An address that an admin has
-- already invited is provisioned active with the invited role and location;
-- anyone else lands in the pending queue and can see nothing until approved.
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_meta jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
  v_email text := lower(new.email);
  v_name text := nullif(trim(coalesce(v_meta ->> 'full_name', '')), '');
  v_requested uuid;
  v_inv public.invitations%rowtype;
begin
  begin
    v_requested := nullif(v_meta ->> 'requested_location_id', '')::uuid;
  exception when others then
    v_requested := null;
  end;

  select * into v_inv
  from public.invitations
  where email = v_email and accepted_at is null and revoked_at is null
  limit 1;

  if found then
    insert into public.app_users (id, email, full_name, role, status, location_id)
    values (new.id, v_email, coalesce(v_name, v_inv.full_name), v_inv.role, 'active', v_inv.location_id);

    update public.invitations
    set accepted_at = now(), accepted_by = new.id
    where id = v_inv.id;

    insert into public.audit_log (actor_id, actor_location_id, action, entity_type, entity_id, new_value)
    values (new.id, v_inv.location_id, 'user.invitation_accepted', 'app_users', new.id::text,
            jsonb_build_object('email', v_email, 'role', v_inv.role, 'location_id', v_inv.location_id));
  else
    insert into public.app_users (id, email, full_name, role, status, location_id)
    values (new.id, v_email, coalesce(v_name, split_part(v_email, '@', 1)), 'employee', 'pending', v_requested);

    insert into public.audit_log (actor_id, actor_location_id, action, entity_type, entity_id, new_value)
    values (new.id, v_requested, 'user.access_requested', 'app_users', new.id::text,
            jsonb_build_object('email', v_email, 'requested_location_id', v_requested));
  end if;

  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_auth_user();

-- Loads the caller's row and refuses anyone who is not active.
create or replace function public.require_active_user()
returns public.app_users
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
begin
  select * into v_user from public.app_users where id = auth.uid();
  if not found or v_user.status <> 'active' then
    raise exception 'Your account is not active' using errcode = '42501';
  end if;
  return v_user;
end;
$$;

create or replace function public.require_admin()
returns public.app_users
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
begin
  v_user := public.require_active_user();
  if v_user.role <> 'admin' then
    raise exception 'Administrator access required' using errcode = '42501';
  end if;
  return v_user;
end;
$$;

-- ------------------------------------------------------ record presence ----
create or replace function public.touch_presence()
returns timestamptz
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := now();
begin
  update public.app_users set last_seen_at = v_now where id = auth.uid();
  return v_now;
end;
$$;

-- --------------------------------------------- pending user picks a place ----
-- The only write a pending account can make. Once an admin has approved the
-- account, the location is theirs to set, not the employee's.
create or replace function public.set_requested_location(p_location_id uuid)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
begin
  select * into v_user from public.app_users where id = auth.uid();
  if not found then
    raise exception 'No account for this session' using errcode = '42501';
  end if;
  if v_user.status <> 'pending' then
    raise exception 'An administrator sets your location' using errcode = '42501';
  end if;

  perform 1 from public.locations where id = p_location_id and active;
  if not found then
    raise exception 'Unknown or inactive location' using errcode = '22023';
  end if;

  update public.app_users set location_id = p_location_id where id = v_user.id;

  perform public.write_audit('user.location_requested', 'app_users', v_user.id::text,
    jsonb_build_object('location_id', v_user.location_id),
    jsonb_build_object('location_id', p_location_id));

  return p_location_id;
end;
$$;

-- ------------------------------------------------------ create transfer ----
-- p_items: [{"item_id": "<uuid>", "quantity": 2}, ...]
create or replace function public.create_transfer(
  p_kind public.transfer_kind,
  p_counterparty_location_id uuid,
  p_items jsonb,
  p_note text default null,
  p_acting_location_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
  v_acting uuid;
  v_from uuid;
  v_to uuid;
  v_transfer_id uuid;
  v_item_id uuid;
  v_qty numeric;
  v_item public.inventory_items%rowtype;
  v_lines int := 0;
begin
  v_user := public.require_active_user();
  v_acting := coalesce(p_acting_location_id, v_user.location_id);

  if v_acting is null then
    raise exception 'Pick the location you are working at' using errcode = '22023';
  end if;
  if v_user.role <> 'admin' and v_acting is distinct from v_user.location_id then
    raise exception 'You can only record transfers for your own location' using errcode = '42501';
  end if;
  if p_counterparty_location_id is null or p_counterparty_location_id = v_acting then
    raise exception 'Choose a different location' using errcode = '22023';
  end if;

  perform 1 from public.locations where id = v_acting and active;
  if not found then
    raise exception 'Unknown or inactive location' using errcode = '22023';
  end if;
  perform 1 from public.locations where id = p_counterparty_location_id and active;
  if not found then
    raise exception 'Unknown or inactive location' using errcode = '22023';
  end if;

  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    raise exception 'Add at least one item' using errcode = '22023';
  end if;

  if p_kind = 'take' then
    v_from := p_counterparty_location_id;
    v_to := v_acting;
  else
    v_from := v_acting;
    v_to := p_counterparty_location_id;
  end if;

  insert into public.transfers (
    kind, from_location_id, to_location_id, recorded_by, recorded_by_location_id, note
  )
  values (
    p_kind, v_from, v_to, v_user.id, v_acting, nullif(trim(coalesce(p_note, '')), '')
  )
  returning id into v_transfer_id;

  -- Duplicated item ids in one submission are summed rather than rejected.
  for v_item_id, v_qty in
    select (e ->> 'item_id')::uuid, sum((e ->> 'quantity')::numeric)
    from jsonb_array_elements(p_items) e
    group by 1
  loop
    if v_item_id is null then
      raise exception 'Every line needs an item' using errcode = '22023';
    end if;
    if v_qty is null or v_qty <= 0 then
      raise exception 'Quantity must be greater than zero' using errcode = '22023';
    end if;

    select * into v_item from public.inventory_items where id = v_item_id and active;
    if not found then
      raise exception 'Unknown or inactive item' using errcode = '22023';
    end if;
    if v_item.location_id is not null and v_item.location_id not in (v_from, v_to) then
      raise exception 'That item does not exist at either location' using errcode = '22023';
    end if;

    insert into public.transfer_items (transfer_id, item_id, quantity, unit)
    values (v_transfer_id, v_item_id, round(v_qty, 2), v_item.unit);
    v_lines := v_lines + 1;
  end loop;

  perform public.write_audit(
    'transfer.recorded', 'transfers', v_transfer_id::text, null,
    jsonb_build_object(
      'kind', p_kind, 'from_location_id', v_from, 'to_location_id', v_to,
      'lines', v_lines, 'items', p_items, 'note', p_note
    )
  );

  return v_transfer_id;
end;
$$;

-- ----------------------------------------------------- confirm transfer ----
create or replace function public.confirm_transfer(
  p_transfer_id uuid,
  p_note text default null
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
  v_transfer public.transfers%rowtype;
  v_id uuid;
begin
  v_user := public.require_active_user();

  select * into v_transfer from public.transfers where id = p_transfer_id;
  if not found then
    raise exception 'Transfer not found' using errcode = '42501';
  end if;
  if v_user.role <> 'admin'
     and v_transfer.confirming_location_id is distinct from v_user.location_id then
    raise exception 'Only the other location can confirm this transfer' using errcode = '42501';
  end if;
  if exists (select 1 from public.transfer_adjustments a
             where a.transfer_id = p_transfer_id and a.kind = 'void') then
    raise exception 'That transfer was voided' using errcode = '22023';
  end if;
  if exists (select 1 from public.transfer_confirmations c where c.transfer_id = p_transfer_id) then
    raise exception 'That transfer is already confirmed' using errcode = '23505';
  end if;

  insert into public.transfer_confirmations (transfer_id, confirmed_by, note)
  values (p_transfer_id, v_user.id, nullif(trim(coalesce(p_note, '')), ''))
  returning id into v_id;

  perform public.write_audit(
    'transfer.confirmed', 'transfers', p_transfer_id::text, null,
    jsonb_build_object('confirmed_by', v_user.id, 'note', p_note)
  );

  return v_id;
end;
$$;

-- --------------------------------------------------------- corrections ----
-- Corrections never touch the original rows. They append an adjustment that
-- the read models fold in when computing effective quantities.
create or replace function public.adjust_transfer_item(
  p_transfer_item_id uuid,
  p_new_quantity numeric,
  p_reason text
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
  v_line public.transfer_items%rowtype;
  v_previous numeric;
  v_id uuid;
begin
  v_user := public.require_admin();

  if nullif(trim(coalesce(p_reason, '')), '') is null then
    raise exception 'A reason is required for a correction' using errcode = '22023';
  end if;
  if p_new_quantity is null or p_new_quantity < 0 then
    raise exception 'New quantity must be zero or more' using errcode = '22023';
  end if;

  select * into v_line from public.transfer_items where id = p_transfer_item_id;
  if not found then
    raise exception 'Transfer line not found' using errcode = '22023';
  end if;

  select coalesce(
    (select a.new_quantity from public.transfer_adjustments a
      where a.transfer_item_id = v_line.id and a.kind = 'quantity'
      order by a.created_at desc, a.id desc limit 1),
    v_line.quantity
  ) into v_previous;

  insert into public.transfer_adjustments (
    transfer_id, transfer_item_id, kind, previous_quantity, new_quantity, reason, created_by
  )
  values (
    v_line.transfer_id, v_line.id, 'quantity', v_previous, round(p_new_quantity, 2),
    trim(p_reason), v_user.id
  )
  returning id into v_id;

  perform public.write_audit(
    'transfer.line_corrected', 'transfer_items', v_line.id::text,
    jsonb_build_object('quantity', v_previous),
    jsonb_build_object('quantity', round(p_new_quantity, 2), 'reason', trim(p_reason))
  );

  return v_id;
end;
$$;

create or replace function public.void_transfer(
  p_transfer_id uuid,
  p_reason text
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
  v_transfer public.transfers%rowtype;
  v_id uuid;
begin
  v_user := public.require_admin();

  if nullif(trim(coalesce(p_reason, '')), '') is null then
    raise exception 'A reason is required to void a transfer' using errcode = '22023';
  end if;

  select * into v_transfer from public.transfers where id = p_transfer_id;
  if not found then
    raise exception 'Transfer not found' using errcode = '22023';
  end if;
  if exists (select 1 from public.transfer_adjustments a
             where a.transfer_id = p_transfer_id and a.kind = 'void') then
    raise exception 'That transfer is already voided' using errcode = '23505';
  end if;

  insert into public.transfer_adjustments (transfer_id, kind, reason, created_by)
  values (p_transfer_id, 'void', trim(p_reason), v_user.id)
  returning id into v_id;

  perform public.write_audit(
    'transfer.voided', 'transfers', p_transfer_id::text,
    to_jsonb(v_transfer), jsonb_build_object('reason', trim(p_reason))
  );

  return v_id;
end;
$$;
