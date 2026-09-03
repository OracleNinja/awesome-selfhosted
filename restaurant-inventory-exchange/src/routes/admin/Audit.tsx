import { Empty, List, Notice, Row, Screen, Spinner } from '../../ui';
import { useAsync } from '../../state/useAsync';
import { fullWhen } from '../../lib/format';
import * as api from '../../lib/api';

const ACTION_LABEL: Record<string, string> = {
  'transfer.recorded': 'Transfer recorded',
  'transfer.confirmed': 'Transfer confirmed',
  'transfer.line_corrected': 'Quantity corrected',
  'transfer.voided': 'Transfer voided',
  'user.updated': 'Account changed',
  'user.access_requested': 'Access requested',
  'user.location_requested': 'Location requested',
  'user.invitation_accepted': 'Invitation accepted',
  'user.bootstrapped_admin': 'First admin created',
  'invitation.created': 'Invitation sent',
  'invitation.revoked': 'Invitation revoked',
  'location.created': 'Location added',
  'location.updated': 'Location changed',
  'item.created': 'Item added',
  'item.updated': 'Item changed',
  'setting.updated': 'Setting changed',
};

export default function Audit() {
  const entries = useAsync(() => api.fetchAuditLog(200), []);
  const users = useAsync(() => api.fetchUsers(), []);

  const name = (id: string | null) =>
    users.data?.find((u) => u.id === id)?.full_name ?? 'System';

  return (
    <Screen title="Audit trail" back="/admin">
      <p className="page-subtitle" style={{ marginTop: 16 }}>
        Every change, oldest value on the left. Nothing here can be edited or removed.
      </p>
      {entries.loading && <Spinner />}
      {entries.error && <Notice>{entries.error}</Notice>}
      {!entries.loading && (entries.data ?? []).length === 0 && <Empty>Nothing yet.</Empty>}

      <List>
        {(entries.data ?? []).map((entry) => (
          <Row
            key={entry.id}
            title={ACTION_LABEL[entry.action] ?? entry.action}
            subtitle={`${name(entry.actor_id)} · ${fullWhen(entry.created_at)}${change(entry.old_value, entry.new_value)}`}
          />
        ))}
      </List>
    </Screen>
  );
}

/** " · quantity 5 → 2" when both values are present and comparable. */
function change(oldValue: unknown, newValue: unknown): string {
  if (!isRecord(oldValue) || !isRecord(newValue)) return '';
  const parts: string[] = [];
  for (const key of Object.keys(newValue)) {
    if (!(key in oldValue)) continue;
    const before = oldValue[key];
    const after = newValue[key];
    if (before === after || before === null || after === null) continue;
    if (typeof before === 'object' || typeof after === 'object') continue;
    parts.push(`${key} ${String(before)} → ${String(after)}`);
  }
  return parts.length > 0 ? ` · ${parts.join(', ')}` : '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
