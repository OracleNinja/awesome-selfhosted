import { List, Notice, Row, Screen, SectionLabel, Spinner, Tag } from '../../ui';
import { useAsync } from '../../state/useAsync';
import { isOnline } from '../../lib/format';
import * as api from '../../lib/api';

export default function AdminHome() {
  const users = useAsync(() => api.fetchUsers(), []);
  const locations = useAsync(() => api.fetchLocations(true), []);

  const pending = (users.data ?? []).filter((u) => u.status === 'pending');
  const active = (users.data ?? []).filter((u) => u.status === 'active');
  const online = active.filter((u) => isOnline(u.last_seen_at));

  return (
    <Screen title="Admin" back="/">
      <h1 className="page-title">Admin</h1>
      <p className="page-subtitle">Everything that is not recording a transfer.</p>

      {(users.loading || locations.loading) && <Spinner />}
      {users.error && <Notice>{users.error}</Notice>}

      {pending.length > 0 && (
        <>
          <SectionLabel>Needs your attention</SectionLabel>
          <List>
            <Row
              title="Pending access requests"
              subtitle={pending.map((u) => u.full_name).join(', ')}
              href="/admin/users"
              trailing={<Tag tone="pending">{pending.length}</Tag>}
            />
          </List>
        </>
      )}

      <SectionLabel>Manage</SectionLabel>
      <List>
        <Row
          title="Users"
          subtitle={`${active.length} active · ${online.length} here now`}
          href="/admin/users"
        />
        <Row title="Locations" subtitle={`${locations.data?.length ?? 0} set up`} href="/admin/locations" />
        <Row title="Inventory catalog" href="/admin/catalog" />
      </List>

      <SectionLabel>Look at</SectionLabel>
      <List>
        <Row title="Transfer activity" subtitle="Filter by shop, person, item or date" href="/admin/activity" />
        <Row title="Balances" subtitle="What each shop owes another" href="/admin/balances" />
        <Row title="Audit trail" subtitle="Every change, with old and new values" href="/admin/audit" />
      </List>
    </Screen>
  );
}
