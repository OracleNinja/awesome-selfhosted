import { useNavigate } from 'react-router-dom';
import { Action, List, Row, SectionLabel, Tag } from '../ui';
import { useAsync } from '../state/useAsync';
import { useSession } from '../state/session';
import * as api from '../lib/api';

/**
 * The first screen after signing in. Four things you can do, in the order an
 * employee actually needs them, and nothing else competing for attention.
 */
export default function Home() {
  const navigate = useNavigate();
  const { profile } = useSession();
  const locationId = profile?.location_id ?? null;

  const locations = useAsync(() => api.fetchLocations(), []);
  const waiting = useAsync(
    () => (locationId ? api.fetchAwaitingConfirmation(locationId) : Promise.resolve([])),
    [locationId],
  );

  const myLocation = locations.data?.find((l) => l.id === locationId);
  const firstName = profile?.full_name.split(' ')[0] ?? '';
  const pendingCount = waiting.data?.length ?? 0;

  return (
    <div className="screen">
      <div className="screen__body" style={{ paddingTop: 'calc(24px + env(safe-area-inset-top))' }}>
        <h1 className="page-title">{firstName ? `Hi ${firstName}` : 'Inventory Exchange'}</h1>
        <p className="page-subtitle">
          {myLocation ? (
            <>
              Working at <strong style={{ color: 'var(--text)' }}>{myLocation.name}</strong>
            </>
          ) : (
            'No location set yet.'
          )}
        </p>

        <div className="stack">
          <Action
            label="Take something"
            hint="You are picking it up from another shop"
            onClick={() => navigate('/record/take')}
          />
          <Action
            label="Give something"
            hint="You are sending it to another shop"
            onClick={() => navigate('/record/give')}
          />
          <Action
            label="Receive / confirm"
            hint={pendingCount === 0 ? 'Nothing waiting' : undefined}
            badge={pendingCount > 0 ? <Tag tone="pending">{pendingCount} waiting</Tag> : undefined}
            onClick={() => navigate('/confirm')}
          />
          <Action label="History" onClick={() => navigate('/history')} />
        </div>

        {profile?.role === 'admin' && (
          <>
            <SectionLabel>Administration</SectionLabel>
            <List>
              <Row title="Admin" subtitle="Users, locations, catalog, activity" href="/admin" />
            </List>
          </>
        )}

        <SectionLabel>Account</SectionLabel>
        <List>
          <Row title="Signed in as" value={profile?.full_name} />
          <Row title="Role" value={roleLabel(profile?.role)} />
          <Row title="Sign out" onClick={() => void api.signOut()} trailing={<span />} />
        </List>
      </div>
    </div>
  );
}

function roleLabel(role: string | undefined): string {
  if (role === 'admin') return 'Admin';
  if (role === 'manager') return 'Manager';
  return 'Employee';
}
