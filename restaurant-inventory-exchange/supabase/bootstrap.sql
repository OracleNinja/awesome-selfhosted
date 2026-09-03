-- Restaurant Inventory Exchange: complete database setup.
--
-- GENERATED FILE. Rebuild with `npm run db:bundle`; edit the files in
-- supabase/migrations/ instead.
--
-- Paste the whole thing into the Supabase SQL editor of a NEW project and run
-- it once. It creates the schema, the row level security policies, the read
-- models, the business functions, and the starting locations and catalog.
--
-- Bundled migrations:
--   0001_schema.sql
--   0002_security_helpers.sql
--   0003_rls.sql
--   0004_views.sql
--   0005_rpc.sql
--   0006_admin_rpc.sql
--   0007_seed_reference_data.sql

-- ==========================================================================
-- 0001_schema.sql
-- ==========================================================================

-- Restaurant Inventory Exchange - core relational schema.
-- Runs on Supabase Postgres. The `auth` schema is provided by Supabase; the
-- test harness creates an equivalent shim so these migrations run unmodified.

-- ---------------------------------------------------------------- enums ----
create type public.user_role as enum ('admin', 'manager', 'employee');
create type public.user_status as enum ('pending', 'active', 'disabled');
create type public.transfer_kind as enum ('take', 'give');
create type public.adjustment_kind as enum ('quantity', 'void');

-- ------------------------------------------------------------ locations ----
create table public.locations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create unique index locations_name_key on public.locations (lower(name));

-- ------------------------------------------------------------ app users ----
-- One row per authenticated user. `id` mirrors auth.users.id.
create table public.app_users (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  full_name text not null,
  role public.user_role not null default 'employee',
  status public.user_status not null default 'pending',
  location_id uuid references public.locations (id) on delete set null,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  constraint app_users_email_lowercase check (email = lower(email))
);
create unique index app_users_email_key on public.app_users (email);
create index app_users_location_idx on public.app_users (location_id);

-- ---------------------------------------------------------- invitations ----
-- An admin pre-authorises an email address. When that address signs up the
-- account is provisioned with the invited role/location and is active
-- immediately. No secret token is involved, so nothing can leak: the invitee
-- must control the mailbox to complete Supabase's own signup verification.
create table public.invitations (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  full_name text not null,
  role public.user_role not null default 'employee',
  location_id uuid not null references public.locations (id),
  invited_by uuid references public.app_users (id),
  accepted_at timestamptz,
  accepted_by uuid references public.app_users (id),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  constraint invitations_email_lowercase check (email = lower(email))
);
create unique index invitations_open_email_key on public.invitations (email)
  where accepted_at is null and revoked_at is null;

-- ----------------------------------------------------- inventory catalog ----
-- location_id null  => shared catalog item (the norm).
-- location_id set   => item that only exists at one location (future support).
create table public.inventory_items (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  category text not null default 'General',
  unit text not null default 'each',
  sku text,
  active boolean not null default true,
  notes text,
  location_id uuid references public.locations (id) on delete cascade,
  created_at timestamptz not null default now()
);
create unique index inventory_items_scope_key on public.inventory_items
  (lower(name), coalesce(location_id, '00000000-0000-0000-0000-000000000000'::uuid));
create index inventory_items_category_idx on public.inventory_items (category);

-- ------------------------------------------------------------ transfers ----
-- Transfers are append-only records. There are no UPDATE or DELETE policies
-- on this table or its children; corrections are new adjustment rows.
create table public.transfers (
  id uuid primary key default gen_random_uuid(),
  kind public.transfer_kind not null,
  from_location_id uuid not null references public.locations (id),
  to_location_id uuid not null references public.locations (id),
  recorded_by uuid not null references public.app_users (id),
  recorded_by_location_id uuid not null references public.locations (id),
  recorded_at timestamptz not null default now(),
  note text,
  -- The counterparty that did not record the transfer is the one that
  -- confirms it: a "give" is confirmed by the receiver, a "take" is
  -- acknowledged by the location the goods came from.
  confirming_location_id uuid generated always as (
    case when kind = 'take' then from_location_id else to_location_id end
  ) stored,
  constraint transfers_distinct_locations check (from_location_id <> to_location_id)
);
create index transfers_from_idx on public.transfers (from_location_id, recorded_at desc);
create index transfers_to_idx on public.transfers (to_location_id, recorded_at desc);
create index transfers_recorded_by_idx on public.transfers (recorded_by, recorded_at desc);
create index transfers_confirming_idx on public.transfers (confirming_location_id, recorded_at desc);

create table public.transfer_items (
  id uuid primary key default gen_random_uuid(),
  transfer_id uuid not null references public.transfers (id) on delete restrict,
  item_id uuid not null references public.inventory_items (id),
  quantity numeric(12, 2) not null check (quantity > 0),
  unit text not null,
  unique (transfer_id, item_id)
);
create index transfer_items_transfer_idx on public.transfer_items (transfer_id);

create table public.transfer_confirmations (
  id uuid primary key default gen_random_uuid(),
  transfer_id uuid not null unique references public.transfers (id) on delete restrict,
  confirmed_by uuid not null references public.app_users (id),
  confirmed_at timestamptz not null default now(),
  note text
);

-- Corrections. The original transfer row is never touched.
create table public.transfer_adjustments (
  id uuid primary key default gen_random_uuid(),
  transfer_id uuid not null references public.transfers (id) on delete restrict,
  transfer_item_id uuid references public.transfer_items (id) on delete restrict,
  kind public.adjustment_kind not null,
  previous_quantity numeric(12, 2),
  new_quantity numeric(12, 2),
  reason text not null,
  created_by uuid not null references public.app_users (id),
  created_at timestamptz not null default now(),
  constraint transfer_adjustments_shape check (
    (kind = 'quantity'
      and transfer_item_id is not null
      and new_quantity is not null
      and new_quantity >= 0)
    or
    (kind = 'void' and transfer_item_id is null)
  )
);
create index transfer_adjustments_transfer_idx on public.transfer_adjustments (transfer_id);
create index transfer_adjustments_item_idx on public.transfer_adjustments (transfer_item_id);

-- ------------------------------------------------------------ audit log ----
create table public.audit_log (
  id bigint generated always as identity primary key,
  actor_id uuid references public.app_users (id),
  actor_location_id uuid references public.locations (id),
  action text not null,
  entity_type text not null,
  entity_id text,
  old_value jsonb,
  new_value jsonb,
  created_at timestamptz not null default now()
);
create index audit_log_created_idx on public.audit_log (created_at desc);
create index audit_log_entity_idx on public.audit_log (entity_type, entity_id);

-- ------------------------------------------------------------- settings ----
create table public.app_settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by uuid references public.app_users (id)
);
insert into public.app_settings (key, value) values ('pricing_enabled', 'false'::jsonb);

-- ==========================================================================
-- 0002_security_helpers.sql
-- ==========================================================================

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

-- ==========================================================================
-- 0003_rls.sql
-- ==========================================================================

-- Row level security. Nothing in this application relies on the frontend for
-- authorisation: every table is readable only through the policies below, and
-- every write goes through a SECURITY DEFINER function that re-checks the
-- caller's role. Tables deliberately have no UPDATE/DELETE policies.

alter table public.locations             enable row level security;
alter table public.app_users             enable row level security;
alter table public.invitations           enable row level security;
alter table public.inventory_items       enable row level security;
alter table public.transfers             enable row level security;
alter table public.transfer_items        enable row level security;
alter table public.transfer_confirmations enable row level security;
alter table public.transfer_adjustments  enable row level security;
alter table public.audit_log             enable row level security;
alter table public.app_settings          enable row level security;

-- Start from zero: the anonymous role gets nothing at all, and the signed-in
-- role gets SELECT only. Writes happen exclusively through RPCs.
revoke all on all tables in schema public from anon, authenticated;

grant select on public.locations              to authenticated;
grant select on public.app_users              to authenticated;
grant select on public.invitations            to authenticated;
grant select on public.inventory_items        to authenticated;
grant select on public.transfers              to authenticated;
grant select on public.transfer_items         to authenticated;
grant select on public.transfer_confirmations to authenticated;
grant select on public.transfer_adjustments   to authenticated;
grant select on public.audit_log              to authenticated;
grant select on public.app_settings           to authenticated;

-- ------------------------------------------------------------ locations ----
-- Pending accounts can read this one table and nothing else, because the
-- first thing they are asked is which restaurant they work at.
create policy locations_read on public.locations
  for select to authenticated
  using (public.is_not_disabled());

-- ------------------------------------------------------------ app_users ----
-- Always your own row (so a pending user can see they are pending), your
-- location's roster if you are a manager, everything if you are an admin.
create policy app_users_read on public.app_users
  for select to authenticated
  using (
    id = auth.uid()
    or public.is_admin()
    or (
      public.my_role() = 'manager'
      and location_id is not distinct from public.my_location()
    )
  );

-- ---------------------------------------------------------- invitations ----
create policy invitations_read on public.invitations
  for select to authenticated
  using (public.is_admin());

-- ---------------------------------------------------- inventory catalog ----
create policy inventory_items_read on public.inventory_items
  for select to authenticated
  using (public.is_active_user());

-- ------------------------------------------------------------ transfers ----
create policy transfers_read on public.transfers
  for select to authenticated
  using (
    public.is_active_user()
    and (
      public.is_admin()
      or recorded_by = auth.uid()
      or from_location_id = public.my_location()
      or to_location_id = public.my_location()
    )
  );

create policy transfer_items_read on public.transfer_items
  for select to authenticated
  using (public.can_see_transfer(transfer_id));

create policy transfer_confirmations_read on public.transfer_confirmations
  for select to authenticated
  using (public.can_see_transfer(transfer_id));

create policy transfer_adjustments_read on public.transfer_adjustments
  for select to authenticated
  using (public.can_see_transfer(transfer_id));

-- ------------------------------------------------------------ audit log ----
create policy audit_log_read on public.audit_log
  for select to authenticated
  using (public.is_admin());

-- ------------------------------------------------------------- settings ----
create policy app_settings_read on public.app_settings
  for select to authenticated
  using (public.is_active_user());

-- ==========================================================================
-- 0004_views.sql
-- ==========================================================================

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

-- ==========================================================================
-- 0005_rpc.sql
-- ==========================================================================

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

-- ==========================================================================
-- 0006_admin_rpc.sql
-- ==========================================================================

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
grant execute on function public.is_not_disabled()        to authenticated;
grant execute on function public.set_requested_location(uuid) to authenticated;
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

-- ==========================================================================
-- 0007_seed_reference_data.sql
-- ==========================================================================

-- Starting locations and catalog. Nothing in the application is coupled to
-- these rows; they are simply a useful first day. Admins add, rename and
-- deactivate locations and items from the app.

insert into public.locations (name)
select v.name
from (values ('Hibachio 1'), ('Hibachio 2'), ('Hibachio 3'), ('287 Taco Shop')) as v(name)
where not exists (
  select 1 from public.locations l where lower(l.name) = lower(v.name)
);

insert into public.inventory_items (name, category, unit)
select v.name, v.category, v.unit
from (values
  ('32 oz Cups',         'Cups',       'sleeve'),
  ('32 oz Lids',         'Lids',       'sleeve'),
  ('16 oz Cups',         'Cups',       'sleeve'),
  ('16 oz Lids',         'Lids',       'sleeve'),
  ('Napkins',            'Paper',      'case'),
  ('Tortilla Chips',     'Food',       'case'),
  ('Gloves',             'Supplies',   'box'),
  ('Foil',               'Supplies',   'roll'),
  ('Takeout Containers', 'Packaging',  'case'),
  ('Straws',             'Paper',      'box'),
  ('To-Go Bags',         'Packaging',  'bundle')
) as v(name, category, unit)
where not exists (
  select 1 from public.inventory_items i
  where lower(i.name) = lower(v.name) and i.location_id is null
);
