-- Administration. Roles, locations, catalog and invitations. Everything here
-- refuses non-admins and writes an audit row carrying the old and new values.

create or replace function public.admin_upsert_location(
  p_name text,
  p_id uuid default null,
  p_active boolean default true
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin public.app_users%rowtype;
  v_old public.locations%rowtype;
  v_id uuid;
  v_name text := nullif(trim(coalesce(p_name, '')), '');
begin
  v_admin := public.require_admin();
  if v_name is null then
    raise exception 'Location name is required' using errcode = '22023';
  end if;

  if p_id is null then
    insert into public.locations (name, active) values (v_name, coalesce(p_active, true))
    returning id into v_id;
    perform public.write_audit('location.created', 'locations', v_id::text, null,
      jsonb_build_object('name', v_name, 'active', coalesce(p_active, true)));
  else
    select * into v_old from public.locations where id = p_id;
    if not found then
      raise exception 'Location not found' using errcode = '22023';
    end if;
    update public.locations
      set name = v_name, active = coalesce(p_active, v_old.active)
      where id = p_id
      returning id into v_id;
    perform public.write_audit('location.updated', 'locations', v_id::text,
      jsonb_build_object('name', v_old.name, 'active', v_old.active),
      jsonb_build_object('name', v_name, 'active', coalesce(p_active, v_old.active)));
  end if;

  return v_id;
end;
$$;

create or replace function public.admin_upsert_item(
  p_name text,
  p_category text default 'General',
  p_unit text default 'each',
  p_id uuid default null,
  p_sku text default null,
  p_active boolean default true,
  p_notes text default null,
  p_location_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin public.app_users%rowtype;
  v_old public.inventory_items%rowtype;
  v_id uuid;
  v_name text := nullif(trim(coalesce(p_name, '')), '');
  v_unit text := coalesce(nullif(trim(coalesce(p_unit, '')), ''), 'each');
  v_category text := coalesce(nullif(trim(coalesce(p_category, '')), ''), 'General');
begin
  v_admin := public.require_admin();
  if v_name is null then
    raise exception 'Item name is required' using errcode = '22023';
  end if;

  if p_id is null then
    insert into public.inventory_items (name, category, unit, sku, active, notes, location_id)
    values (v_name, v_category, v_unit, nullif(trim(coalesce(p_sku, '')), ''),
            coalesce(p_active, true), nullif(trim(coalesce(p_notes, '')), ''), p_location_id)
    returning id into v_id;
    perform public.write_audit('item.created', 'inventory_items', v_id::text, null,
      jsonb_build_object('name', v_name, 'category', v_category, 'unit', v_unit));
  else
    select * into v_old from public.inventory_items where id = p_id;
    if not found then
      raise exception 'Item not found' using errcode = '22023';
    end if;
    update public.inventory_items
      set name = v_name,
          category = v_category,
          unit = v_unit,
          sku = nullif(trim(coalesce(p_sku, '')), ''),
          active = coalesce(p_active, v_old.active),
          notes = nullif(trim(coalesce(p_notes, '')), ''),
          location_id = p_location_id
      where id = p_id
      returning id into v_id;
    perform public.write_audit('item.updated', 'inventory_items', v_id::text,
      to_jsonb(v_old), jsonb_build_object('name', v_name, 'category', v_category,
        'unit', v_unit, 'active', coalesce(p_active, v_old.active)));
  end if;

  return v_id;
end;
$$;

-- Approve, reject, promote, move or disable an account. Null arguments leave
-- the corresponding field alone.
create or replace function public.admin_update_user(
  p_user_id uuid,
  p_role public.user_role default null,
  p_location_id uuid default null,
  p_status public.user_status default null
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin public.app_users%rowtype;
  v_old public.app_users%rowtype;
  v_role public.user_role;
  v_status public.user_status;
  v_location uuid;
begin
  v_admin := public.require_admin();

  select * into v_old from public.app_users where id = p_user_id;
  if not found then
    raise exception 'User not found' using errcode = '22023';
  end if;

  v_role := coalesce(p_role, v_old.role);
  v_status := coalesce(p_status, v_old.status);
  v_location := coalesce(p_location_id, v_old.location_id);

  if v_location is not null then
    perform 1 from public.locations where id = v_location;
    if not found then
      raise exception 'Location not found' using errcode = '22023';
    end if;
  end if;

  -- Never let the last remaining admin lock the whole organisation out.
  if v_old.role = 'admin' and v_old.status = 'active'
     and (v_role <> 'admin' or v_status <> 'active') then
    if (select count(*) from public.app_users
        where role = 'admin' and status = 'active' and id <> p_user_id) = 0 then
      raise exception 'There must always be at least one active administrator'
        using errcode = '22023';
    end if;
  end if;

  if v_status = 'active' and v_location is null then
    raise exception 'Assign a location before activating this account' using errcode = '22023';
  end if;

  update public.app_users
    set role = v_role, status = v_status, location_id = v_location
    where id = p_user_id;

  perform public.write_audit('user.updated', 'app_users', p_user_id::text,
    jsonb_build_object('role', v_old.role, 'status', v_old.status, 'location_id', v_old.location_id),
    jsonb_build_object('role', v_role, 'status', v_status, 'location_id', v_location));

  return p_user_id;
end;
$$;

create or replace function public.admin_create_invitation(
  p_email text,
  p_full_name text,
  p_location_id uuid,
  p_role public.user_role default 'employee'
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin public.app_users%rowtype;
  v_email text := lower(nullif(trim(coalesce(p_email, '')), ''));
  v_name text := nullif(trim(coalesce(p_full_name, '')), '');
  v_id uuid;
begin
  v_admin := public.require_admin();

  if v_email is null or v_email not like '%_@_%.__%' then
    raise exception 'A valid email address is required' using errcode = '22023';
  end if;
  if v_name is null then
    raise exception 'A name is required' using errcode = '22023';
  end if;
  perform 1 from public.locations where id = p_location_id and active;
  if not found then
    raise exception 'Unknown or inactive location' using errcode = '22023';
  end if;
  if exists (select 1 from public.app_users where email = v_email) then
    raise exception 'That email already has an account' using errcode = '23505';
  end if;

  insert into public.invitations (email, full_name, role, location_id, invited_by)
  values (v_email, v_name, coalesce(p_role, 'employee'), p_location_id, v_admin.id)
  returning id into v_id;

  perform public.write_audit('invitation.created', 'invitations', v_id::text, null,
    jsonb_build_object('email', v_email, 'role', coalesce(p_role, 'employee'),
                       'location_id', p_location_id));

  return v_id;
end;
$$;

create or replace function public.admin_revoke_invitation(p_id uuid)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin public.app_users%rowtype;
  v_old public.invitations%rowtype;
begin
  v_admin := public.require_admin();

  select * into v_old from public.invitations where id = p_id;
  if not found or v_old.accepted_at is not null then
    raise exception 'Invitation not found' using errcode = '22023';
  end if;

  update public.invitations set revoked_at = now() where id = p_id;

  perform public.write_audit('invitation.revoked', 'invitations', p_id::text,
    jsonb_build_object('email', v_old.email), null);

  return p_id;
end;
$$;

create or replace function public.admin_set_setting(p_key text, p_value jsonb)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin public.app_users%rowtype;
  v_old jsonb;
begin
  v_admin := public.require_admin();

  select value into v_old from public.app_settings where key = p_key;

  insert into public.app_settings (key, value, updated_at, updated_by)
  values (p_key, p_value, now(), v_admin.id)
  on conflict (key) do update
    set value = excluded.value, updated_at = now(), updated_by = excluded.updated_by;

  perform public.write_audit('setting.updated', 'app_settings', p_key,
    jsonb_build_object('value', v_old), jsonb_build_object('value', p_value));

  return p_key;
end;
$$;

-- --------------------------------------------------- first admin bootstrap ----
-- Deliberately NOT granted to authenticated. Run it once from the Supabase SQL
-- editor (which connects as postgres) after the first account signs up. There
-- is no universal admin password anywhere in this system.
create or replace function public.bootstrap_admin(p_email text)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user public.app_users%rowtype;
begin
  select * into v_user from public.app_users where email = lower(trim(p_email));
  if not found then
    raise exception 'No account for %. Sign up in the app first, then run this again.', p_email;
  end if;

  update public.app_users
    set role = 'admin', status = 'active'
    where id = v_user.id;

  insert into public.audit_log (actor_id, action, entity_type, entity_id, old_value, new_value)
  values (v_user.id, 'user.bootstrapped_admin', 'app_users', v_user.id::text,
          jsonb_build_object('role', v_user.role, 'status', v_user.status),
          jsonb_build_object('role', 'admin', 'status', 'active'));

  return v_user.id;
end;
$$;

-- ----------------------------------------------------------------- grants ----
revoke all on all functions in schema public from public, anon;

grant execute on function public.current_user_id()        to authenticated;
grant execute on function public.my_role()                to authenticated;
grant execute on function public.my_location()            to authenticated;
grant execute on function public.is_active_user()         to authenticated;
grant execute on function public.is_admin()               to authenticated;
grant execute on function public.is_manager_or_admin()    to authenticated;
grant execute on function public.can_see_transfer(uuid)   to authenticated;
grant execute on function public.touch_presence()         to authenticated;
grant execute on function public.create_transfer(public.transfer_kind, uuid, jsonb, text, uuid) to authenticated;
grant execute on function public.confirm_transfer(uuid, text) to authenticated;
grant execute on function public.adjust_transfer_item(uuid, numeric, text) to authenticated;
grant execute on function public.void_transfer(uuid, text) to authenticated;
grant execute on function public.admin_upsert_location(text, uuid, boolean) to authenticated;
grant execute on function public.admin_upsert_item(text, text, text, uuid, text, boolean, text, uuid) to authenticated;
grant execute on function public.admin_update_user(uuid, public.user_role, uuid, public.user_status) to authenticated;
grant execute on function public.admin_create_invitation(text, text, uuid, public.user_role) to authenticated;
grant execute on function public.admin_revoke_invitation(uuid) to authenticated;
grant execute on function public.admin_set_setting(text, jsonb) to authenticated;
