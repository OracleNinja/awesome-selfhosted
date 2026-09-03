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
