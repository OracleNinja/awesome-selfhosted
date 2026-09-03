import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Empty, List, Notice, Row, Screen, SectionLabel, SelectField, Spinner } from '../../ui';
import { useAsync } from '../../state/useAsync';
import { ROLE_LABEL, STATUS_LABEL, lastSeen } from '../../lib/format';
import * as api from '../../lib/api';
import type { Role } from '../../lib/types';

export default function UserDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const users = useAsync(() => api.fetchUsers(), []);
  const locations = useAsync(() => api.fetchLocations(true), []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const user = users.data?.find((u) => u.id === id) ?? null;

  async function save(changes: { role?: Role; locationId?: string; status?: 'active' | 'disabled' }) {
    setBusy(true);
    setError(null);
    try {
      await api.updateUser({ userId: id, ...changes });
      users.reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen title="User" back="/admin/users">
      {users.loading && <Spinner />}
      {error && <Notice>{error}</Notice>}
      {!users.loading && !user && <Empty>That account is not available.</Empty>}

      {user && (
        <>
          <h1 className="page-title">{user.full_name}</h1>
          <p className="page-subtitle">{user.email}</p>

          <SectionLabel>Access</SectionLabel>
          <div className="card">
            <SelectField
              label="Location"
              value={user.location_id ?? ''}
              disabled={busy}
              onChange={(e) => void save({ locationId: e.target.value })}
            >
              <option value="" disabled>
                Not assigned
              </option>
              {(locations.data ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </SelectField>
            <SelectField
              label="Role"
              value={user.role}
              disabled={busy}
              onChange={(e) => void save({ role: e.target.value as Role })}
            >
              <option value="employee">Employee</option>
              <option value="manager">Manager</option>
              <option value="admin">Admin</option>
            </SelectField>
          </div>

          <SectionLabel>Status</SectionLabel>
          <List>
            <Row title="Account" value={STATUS_LABEL[user.status]} />
            <Row title="Role" value={ROLE_LABEL[user.role]} />
            <Row title="Last active" value={lastSeen(user.last_seen_at) ?? 'Never'} />
          </List>

          <div style={{ marginTop: 24 }}>
            {user.status === 'active' ? (
              <button
                type="button"
                className="btn btn--danger"
                disabled={busy}
                onClick={() => void save({ status: 'disabled' })}
              >
                Turn off this account
              </button>
            ) : (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void save({ status: 'active' })}
              >
                Turn this account back on
              </button>
            )}
          </div>

          <div style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => navigate(`/admin/activity?person=${user.id}`)}
            >
              See their transfers
            </button>
          </div>
        </>
      )}
    </Screen>
  );
}
