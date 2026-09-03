import { useState, type FormEvent } from 'react';
import {
  Empty,
  Field,
  List,
  Notice,
  Row,
  Screen,
  SectionLabel,
  SelectField,
  Spinner,
  Tag,
} from '../../ui';
import { useAsync } from '../../state/useAsync';
import { useSession } from '../../state/session';
import { ROLE_LABEL, isOnline, lastSeen } from '../../lib/format';
import * as api from '../../lib/api';
import type { Profile, Role } from '../../lib/types';

export default function Users() {
  const { profile } = useSession();
  const users = useAsync(() => api.fetchUsers(), []);
  const locations = useAsync(() => api.fetchLocations(true), []);
  const invitations = useAsync(() => api.fetchInvitations(), []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const byStatus = (status: Profile['status']) =>
    (users.data ?? []).filter((u) => u.status === status);

  const locationName = (id: string | null) =>
    locations.data?.find((l) => l.id === id)?.name ?? 'No location';

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      users.reload();
      invitations.reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen title="Users" back="/admin">
      {error && <Notice>{error}</Notice>}
      {(users.loading || locations.loading) && <Spinner />}

      <SectionLabel>Pending access requests</SectionLabel>
      {byStatus('pending').length === 0 ? (
        <Empty>Nobody is waiting.</Empty>
      ) : (
        <div className="stack">
          {byStatus('pending').map((user) => (
            <PendingRequest
              key={user.id}
              user={user}
              locations={locations.data ?? []}
              busy={busy}
              onApprove={(role, locationId) =>
                act(() => api.updateUser({ userId: user.id, role, locationId, status: 'active' }))
              }
              onReject={() => act(() => api.updateUser({ userId: user.id, status: 'disabled' }))}
            />
          ))}
        </div>
      )}

      <SectionLabel>Active</SectionLabel>
      {byStatus('active').length === 0 ? (
        <Empty>No active accounts yet.</Empty>
      ) : (
        <List>
          {byStatus('active').map((user) => (
            <Row
              key={user.id}
              title={user.full_name}
              subtitle={`${ROLE_LABEL[user.role]} · ${locationName(user.location_id)}${
                lastSeen(user.last_seen_at) ? ` · ${lastSeen(user.last_seen_at)}` : ''
              }`}
              href={`/admin/users/${user.id}`}
              trailing={isOnline(user.last_seen_at) ? <Tag tone="ok">Here now</Tag> : undefined}
            />
          ))}
        </List>
      )}

      <SectionLabel>Disabled</SectionLabel>
      {byStatus('disabled').length === 0 ? (
        <Empty>Nobody is disabled.</Empty>
      ) : (
        <List>
          {byStatus('disabled').map((user) => (
            <Row
              key={user.id}
              title={user.full_name}
              subtitle={locationName(user.location_id)}
              value="Disabled"
              onClick={() =>
                act(() =>
                  api.updateUser({
                    userId: user.id,
                    status: 'active',
                    locationId: user.location_id,
                  }),
                )
              }
              trailing={<span className="linkbtn">Re-enable</span>}
            />
          ))}
        </List>
      )}

      <SectionLabel>Open invitations</SectionLabel>
      {(invitations.data ?? []).length === 0 ? (
        <Empty>No invitations outstanding.</Empty>
      ) : (
        <List>
          {(invitations.data ?? []).map((invite) => (
            <Row
              key={invite.id}
              title={invite.full_name}
              subtitle={`${invite.email} · ${ROLE_LABEL[invite.role]} · ${locationName(invite.location_id)}`}
              onClick={() => act(() => api.revokeInvitation(invite.id))}
              trailing={<span className="linkbtn linkbtn--danger">Revoke</span>}
            />
          ))}
        </List>
      )}

      <InviteForm
        locations={locations.data ?? []}
        busy={busy}
        onInvite={(input) => act(() => api.createInvitation(input).then(() => undefined))}
      />

      {profile?.role === 'admin' && (
        <p className="meta" style={{ marginTop: 24 }}>
          An invited person signs up with the email you enter here and is active straight away,
          with the role and location you chose. Nobody else can use that invitation.
        </p>
      )}
    </Screen>
  );
}

function PendingRequest({
  user,
  locations,
  busy,
  onApprove,
  onReject,
}: {
  user: Profile;
  locations: Array<{ id: string; name: string }>;
  busy: boolean;
  onApprove: (role: Role, locationId: string) => void;
  onReject: () => void;
}) {
  const [role, setRole] = useState<Role>('employee');
  const [locationId, setLocationId] = useState(user.location_id ?? locations[0]?.id ?? '');

  return (
    <div className="card">
      <div className="list__row list__row--static" style={{ display: 'block' }}>
        <span className="list__title">{user.full_name}</span>
        <span className="list__sub">{user.email}</span>
      </div>
      <SelectField label="Location" value={locationId} onChange={(e) => setLocationId(e.target.value)}>
        {locations.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
      </SelectField>
      <SelectField label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
        <option value="employee">Employee</option>
        <option value="manager">Manager</option>
        <option value="admin">Admin</option>
      </SelectField>
      <div style={{ display: 'flex', gap: 12, padding: 16 }}>
        <button
          type="button"
          className="btn"
          disabled={busy || !locationId}
          onClick={() => onApprove(role, locationId)}
        >
          Approve
        </button>
        <button type="button" className="btn btn--danger" disabled={busy} onClick={onReject}>
          Reject
        </button>
      </div>
    </div>
  );
}

function InviteForm({
  locations,
  busy,
  onInvite,
}: {
  locations: Array<{ id: string; name: string }>;
  busy: boolean;
  onInvite: (input: { email: string; fullName: string; locationId: string; role: Role }) => void;
}) {
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('employee');
  const [locationId, setLocationId] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    onInvite({ fullName, email, role, locationId: locationId || (locations[0]?.id ?? '') });
    setFullName('');
    setEmail('');
  }

  return (
    <form onSubmit={submit}>
      <SectionLabel>Invite someone</SectionLabel>
      <div className="card">
        <Field label="Name" value={fullName} required onChange={(e) => setFullName(e.target.value)} />
        <Field
          label="Email"
          type="email"
          inputMode="email"
          autoCapitalize="off"
          value={email}
          required
          onChange={(e) => setEmail(e.target.value)}
        />
        <SelectField
          label="Location"
          value={locationId}
          onChange={(e) => setLocationId(e.target.value)}
        >
          {locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </SelectField>
        <SelectField label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="employee">Employee</option>
          <option value="manager">Manager</option>
          <option value="admin">Admin</option>
        </SelectField>
      </div>
      <div style={{ marginTop: 12 }}>
        <button type="submit" className="btn" disabled={busy || locations.length === 0}>
          Send invitation
        </button>
      </div>
    </form>
  );
}
