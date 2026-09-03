-- Helper functions used by row level security policies.
-- They are SECURITY DEFINER so that reading app_users from inside a policy on
-- app_users does not recurse, and they pin search_path so they cannot be
-- hijacked by a caller-controlled path.

create or replace function public.current_user_id()
returns uuid
language sql
stable
as $$
  select auth.uid()
$$;

create or replace function public.my_role()
returns public.user_role
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select u.role from public.app_users u
  where u.id = auth.uid() and u.status = 'active'
$$;

create or replace function public.my_location()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select u.location_id from public.app_users u
  where u.id = auth.uid() and u.status = 'active'
$$;

create or replace function public.is_active_user()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.app_users u
    where u.id = auth.uid() and u.status = 'active'
  )
$$;

-- A signed-up account that has not been switched off. Pending accounts pass:
-- they need to see the list of locations to say where they work.
create or replace function public.is_not_disabled()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.app_users u
    where u.id = auth.uid() and u.status <> 'disabled'
  )
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(public.my_role() = 'admin', false)
$$;

create or replace function public.is_manager_or_admin()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(public.my_role() in ('admin', 'manager'), false)
$$;

-- True when the current user is allowed to see a given transfer: admins see
-- everything, everyone else sees transfers touching their own location (or
-- that they personally recorded).
create or replace function public.can_see_transfer(p_transfer_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.transfers t
    where t.id = p_transfer_id
      and public.is_active_user()
      and (
        public.is_admin()
        or t.recorded_by = auth.uid()
        or t.from_location_id = public.my_location()
        or t.to_location_id = public.my_location()
      )
  )
$$;

-- Append-only audit trail writer. Only callable from other definer functions.
create or replace function public.write_audit(
  p_action text,
  p_entity_type text,
  p_entity_id text,
  p_old jsonb default null,
  p_new jsonb default null
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.audit_log (
    actor_id, actor_location_id, action, entity_type, entity_id, old_value, new_value
  )
  values (
    auth.uid(),
    (select location_id from public.app_users where id = auth.uid()),
    p_action, p_entity_type, p_entity_id, p_old, p_new
  );
end;
$$;

revoke all on function public.write_audit(text, text, text, jsonb, jsonb) from public;

grant execute on function public.current_user_id() to authenticated;
grant execute on function public.my_role() to authenticated;
grant execute on function public.my_location() to authenticated;
grant execute on function public.is_active_user() to authenticated;
grant execute on function public.is_not_disabled() to authenticated;
grant execute on function public.is_admin() to authenticated;
grant execute on function public.is_manager_or_admin() to authenticated;
grant execute on function public.can_see_transfer(uuid) to authenticated;
