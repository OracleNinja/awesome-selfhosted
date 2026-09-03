import { supabase } from './supabase';
import type {
  AuditEntry,
  Balance,
  InventoryItem,
  Invitation,
  Location,
  Profile,
  Role,
  Transfer,
  TransferKind,
  TransferLine,
  TransferSummary,
  UserStatus,
} from './types';

/**
 * Every call in this file goes through the anon key, so the database decides
 * what comes back. Nothing here is a permission check; the checks live in the
 * row level security policies and in the RPCs.
 */

function unwrap<T>(result: { data: T | null; error: { message: string } | null }): T {
  if (result.error) throw new Error(friendly(result.error.message));
  return result.data as T;
}

/** Turns Postgres noise into something an employee can act on. */
function friendly(message: string): string {
  if (/duplicate key value/i.test(message)) return 'That already exists.';
  if (/violates row-level security|permission denied/i.test(message)) {
    return 'You do not have access to do that.';
  }
  if (/JWT|refresh token/i.test(message)) return 'Your session expired. Sign in again.';
  return message;
}

// ------------------------------------------------------------- profile ----

export async function fetchProfile(userId: string): Promise<Profile | null> {
  const { data, error } = await supabase
    .from('app_users')
    .select('*')
    .eq('id', userId)
    .maybeSingle();
  if (error) throw new Error(friendly(error.message));
  return data as Profile | null;
}

export async function touchPresence(): Promise<void> {
  await supabase.rpc('touch_presence');
}

// ----------------------------------------------------------- reference ----

export async function fetchLocations(includeInactive = false): Promise<Location[]> {
  let query = supabase.from('locations').select('*').order('name');
  if (!includeInactive) query = query.eq('active', true);
  return unwrap(await query) ?? [];
}

export async function fetchItems(includeInactive = false): Promise<InventoryItem[]> {
  let query = supabase.from('inventory_items').select('*').order('name');
  if (!includeInactive) query = query.eq('active', true);
  return unwrap(await query) ?? [];
}

export async function fetchSetting<T>(key: string, fallback: T): Promise<T> {
  const { data, error } = await supabase
    .from('app_settings')
    .select('value')
    .eq('key', key)
    .maybeSingle();
  if (error || !data) return fallback;
  return (data as { value: T }).value;
}

// ----------------------------------------------------------- transfers ----

export type TransferFilters = {
  locationId?: string;
  counterpartyId?: string;
  recordedBy?: string;
  itemId?: string;
  kind?: TransferKind;
  confirmed?: boolean;
  from?: string;
  to?: string;
  limit?: number;
};

export async function fetchTransfers(filters: TransferFilters = {}): Promise<Transfer[]> {
  let query = supabase
    .from('transfer_feed')
    .select('*')
    .order('recorded_at', { ascending: false })
    .limit(filters.limit ?? 100);

  if (filters.locationId && filters.counterpartyId) {
    query = query
      .or(`from_location_id.eq.${filters.locationId},to_location_id.eq.${filters.locationId}`)
      .or(
        `from_location_id.eq.${filters.counterpartyId},to_location_id.eq.${filters.counterpartyId}`,
      );
  } else if (filters.locationId) {
    query = query.or(
      `from_location_id.eq.${filters.locationId},to_location_id.eq.${filters.locationId}`,
    );
  }
  if (filters.recordedBy) query = query.eq('recorded_by', filters.recordedBy);
  if (filters.kind) query = query.eq('kind', filters.kind);
  if (filters.confirmed !== undefined) query = query.eq('confirmed', filters.confirmed);
  if (filters.from) query = query.gte('recorded_at', filters.from);
  if (filters.to) query = query.lte('recorded_at', filters.to);

  const summaries = (unwrap(await query) ?? []) as TransferSummary[];
  if (summaries.length === 0) return [];

  const lines = await fetchLines(summaries.map((t) => t.id));
  const withLines = summaries.map((summary) => ({
    ...summary,
    lines: lines.filter((line) => line.transfer_id === summary.id),
  }));

  // Item filtering happens here rather than in the query because a transfer
  // matches when any of its lines does.
  return filters.itemId
    ? withLines.filter((t) => t.lines.some((l) => l.item_id === filters.itemId))
    : withLines;
}

async function fetchLines(transferIds: string[]): Promise<TransferLine[]> {
  const rows = unwrap(
    await supabase
      .from('transfer_line_feed')
      .select('*')
      .in('transfer_id', transferIds)
      .order('item_name'),
  ) as TransferLine[] | null;
  return (rows ?? []).map((row) => ({
    ...row,
    original_quantity: Number(row.original_quantity),
    effective_quantity: Number(row.effective_quantity),
  }));
}

export async function fetchTransfer(id: string): Promise<Transfer | null> {
  const { data, error } = await supabase
    .from('transfer_feed')
    .select('*')
    .eq('id', id)
    .maybeSingle();
  if (error) throw new Error(friendly(error.message));
  if (!data) return null;
  const lines = await fetchLines([id]);
  return { ...(data as TransferSummary), lines };
}

/** Transfers this location still has to confirm. */
export async function fetchAwaitingConfirmation(locationId: string): Promise<Transfer[]> {
  const summaries = (unwrap(
    await supabase
      .from('transfer_feed')
      .select('*')
      .eq('confirming_location_id', locationId)
      .eq('confirmed', false)
      .eq('voided', false)
      .order('recorded_at', { ascending: false }),
  ) ?? []) as TransferSummary[];
  if (summaries.length === 0) return [];
  const lines = await fetchLines(summaries.map((t) => t.id));
  return summaries.map((s) => ({ ...s, lines: lines.filter((l) => l.transfer_id === s.id) }));
}

export async function createTransfer(input: {
  kind: TransferKind;
  counterpartyLocationId: string;
  items: Array<{ item_id: string; quantity: number }>;
  note?: string | null;
  actingLocationId?: string | null;
}): Promise<string> {
  const { data, error } = await supabase.rpc('create_transfer', {
    p_kind: input.kind,
    p_counterparty_location_id: input.counterpartyLocationId,
    p_items: input.items,
    p_note: input.note ?? null,
    p_acting_location_id: input.actingLocationId ?? null,
  });
  if (error) throw new Error(friendly(error.message));
  return data as string;
}

export async function confirmTransfer(id: string, note?: string | null): Promise<void> {
  const { error } = await supabase.rpc('confirm_transfer', {
    p_transfer_id: id,
    p_note: note ?? null,
  });
  if (error) throw new Error(friendly(error.message));
}

// ---------------------------------------------------------- corrections ----

export async function adjustLine(
  transferItemId: string,
  newQuantity: number,
  reason: string,
): Promise<void> {
  const { error } = await supabase.rpc('adjust_transfer_item', {
    p_transfer_item_id: transferItemId,
    p_new_quantity: newQuantity,
    p_reason: reason,
  });
  if (error) throw new Error(friendly(error.message));
}

export async function voidTransfer(id: string, reason: string): Promise<void> {
  const { error } = await supabase.rpc('void_transfer', {
    p_transfer_id: id,
    p_reason: reason,
  });
  if (error) throw new Error(friendly(error.message));
}

// -------------------------------------------------------------- balances ----

export async function fetchBalances(locationId?: string): Promise<Balance[]> {
  let query = supabase.from('location_item_balances').select('*').order('item_name');
  if (locationId) query = query.eq('location_a', locationId);
  const rows = (unwrap(await query) ?? []) as Balance[];
  return rows.map((row) => ({ ...row, net_quantity: Number(row.net_quantity) }));
}

// ----------------------------------------------------------------- admin ----

export async function fetchUsers(): Promise<Profile[]> {
  return (unwrap(
    await supabase.from('app_users').select('*').order('full_name'),
  ) ?? []) as Profile[];
}

export async function updateUser(input: {
  userId: string;
  role?: Role;
  locationId?: string | null;
  status?: UserStatus;
}): Promise<void> {
  const { error } = await supabase.rpc('admin_update_user', {
    p_user_id: input.userId,
    p_role: input.role ?? null,
    p_location_id: input.locationId ?? null,
    p_status: input.status ?? null,
  });
  if (error) throw new Error(friendly(error.message));
}

export async function upsertLocation(input: {
  name: string;
  id?: string | null;
  active?: boolean;
}): Promise<string> {
  const { data, error } = await supabase.rpc('admin_upsert_location', {
    p_name: input.name,
    p_id: input.id ?? null,
    p_active: input.active ?? true,
  });
  if (error) throw new Error(friendly(error.message));
  return data as string;
}

export async function upsertItem(input: {
  name: string;
  category: string;
  unit: string;
  id?: string | null;
  sku?: string | null;
  active?: boolean;
  notes?: string | null;
  locationId?: string | null;
}): Promise<string> {
  const { data, error } = await supabase.rpc('admin_upsert_item', {
    p_name: input.name,
    p_category: input.category,
    p_unit: input.unit,
    p_id: input.id ?? null,
    p_sku: input.sku ?? null,
    p_active: input.active ?? true,
    p_notes: input.notes ?? null,
    p_location_id: input.locationId ?? null,
  });
  if (error) throw new Error(friendly(error.message));
  return data as string;
}

export async function fetchInvitations(): Promise<Invitation[]> {
  return (unwrap(
    await supabase
      .from('invitations')
      .select('*')
      .is('accepted_at', null)
      .is('revoked_at', null)
      .order('created_at', { ascending: false }),
  ) ?? []) as Invitation[];
}

export async function createInvitation(input: {
  email: string;
  fullName: string;
  locationId: string;
  role: Role;
}): Promise<string> {
  const { data, error } = await supabase.rpc('admin_create_invitation', {
    p_email: input.email,
    p_full_name: input.fullName,
    p_location_id: input.locationId,
    p_role: input.role,
  });
  if (error) throw new Error(friendly(error.message));
  return data as string;
}

export async function revokeInvitation(id: string): Promise<void> {
  const { error } = await supabase.rpc('admin_revoke_invitation', { p_id: id });
  if (error) throw new Error(friendly(error.message));
}

export async function fetchAuditLog(limit = 100): Promise<AuditEntry[]> {
  return (unwrap(
    await supabase
      .from('audit_log')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(limit),
  ) ?? []) as AuditEntry[];
}

export async function setSetting(key: string, value: unknown): Promise<void> {
  const { error } = await supabase.rpc('admin_set_setting', { p_key: key, p_value: value });
  if (error) throw new Error(friendly(error.message));
}

// ------------------------------------------------------------------ auth ----

export async function signIn(email: string, password: string): Promise<void> {
  const { error } = await supabase.auth.signInWithPassword({
    email: email.trim().toLowerCase(),
    password,
  });
  if (error) throw new Error(error.message);
}

export async function signUp(input: {
  email: string;
  password: string;
  fullName: string;
  requestedLocationId: string | null;
}): Promise<{ needsEmailConfirmation: boolean }> {
  const { data, error } = await supabase.auth.signUp({
    email: input.email.trim().toLowerCase(),
    password: input.password,
    options: {
      data: {
        full_name: input.fullName.trim(),
        requested_location_id: input.requestedLocationId,
      },
    },
  });
  if (error) throw new Error(error.message);
  return { needsEmailConfirmation: Boolean(data.user && !data.session) };
}

export async function signOut(): Promise<void> {
  await supabase.auth.signOut();
}

export async function setRequestedLocation(locationId: string): Promise<void> {
  const { error } = await supabase.rpc('set_requested_location', {
    p_location_id: locationId,
  });
  if (error) throw new Error(friendly(error.message));
}
