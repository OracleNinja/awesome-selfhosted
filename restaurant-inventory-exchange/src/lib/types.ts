export type Role = 'admin' | 'manager' | 'employee';
export type UserStatus = 'pending' | 'active' | 'disabled';
export type TransferKind = 'take' | 'give';

export type Profile = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  status: UserStatus;
  location_id: string | null;
  last_seen_at: string | null;
  created_at: string;
};

export type Location = {
  id: string;
  name: string;
  active: boolean;
  created_at: string;
};

export type InventoryItem = {
  id: string;
  name: string;
  category: string;
  unit: string;
  sku: string | null;
  active: boolean;
  notes: string | null;
  location_id: string | null;
};

export type TransferSummary = {
  id: string;
  kind: TransferKind;
  from_location_id: string;
  from_location_name: string;
  to_location_id: string;
  to_location_name: string;
  recorded_by: string;
  recorded_by_name: string | null;
  recorded_at: string;
  note: string | null;
  confirming_location_id: string;
  confirmed: boolean;
  confirmed_at: string | null;
  confirmed_by_name: string | null;
  voided: boolean;
};

export type TransferLine = {
  id: string;
  transfer_id: string;
  item_id: string;
  item_name: string;
  item_category: string;
  unit: string;
  original_quantity: number;
  effective_quantity: number;
  adjusted: boolean;
  voided: boolean;
};

export type Transfer = TransferSummary & { lines: TransferLine[] };

export type Balance = {
  location_a: string;
  location_a_name: string;
  location_b: string;
  location_b_name: string;
  item_id: string;
  item_name: string;
  unit: string;
  net_quantity: number;
};

export type Invitation = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  location_id: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export type AuditEntry = {
  id: number;
  actor_id: string | null;
  actor_location_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  old_value: unknown;
  new_value: unknown;
  created_at: string;
};

/** One line the user has staged but not yet recorded. */
export type DraftLine = {
  item: InventoryItem;
  quantity: number;
};
