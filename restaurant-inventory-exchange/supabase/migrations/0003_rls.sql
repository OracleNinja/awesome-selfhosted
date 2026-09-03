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
