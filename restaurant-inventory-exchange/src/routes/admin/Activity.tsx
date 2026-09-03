import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Empty, Notice, Screen, SectionLabel, SelectField, Spinner } from '../../ui';
import { TransferCard } from '../../ui/TransferCard';
import { useAsync } from '../../state/useAsync';
import * as api from '../../lib/api';
import type { TransferKind } from '../../lib/types';

/**
 * "Show everything Hibachio 2 took from 287 Taco Shop this month" is four
 * dropdowns and nothing else. The filters compose; none of them is required.
 */
export default function Activity() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const locations = useAsync(() => api.fetchLocations(true), []);
  const items = useAsync(() => api.fetchItems(true), []);
  const users = useAsync(() => api.fetchUsers(), []);

  const [range, setRange] = useState<'month' | 'week' | 'all'>('month');
  const location = params.get('location') ?? '';
  const counterparty = params.get('counterparty') ?? '';
  const person = params.get('person') ?? '';
  const item = params.get('item') ?? '';
  const direction = (params.get('direction') ?? '') as '' | TransferKind;
  const state = params.get('state') ?? '';

  const from = useMemo(() => startOf(range), [range]);

  const transfers = useAsync(
    () =>
      api.fetchTransfers({
        locationId: location || undefined,
        counterpartyId: counterparty || undefined,
        recordedBy: person || undefined,
        itemId: item || undefined,
        kind: direction || undefined,
        confirmed: state === '' ? undefined : state === 'confirmed',
        from: from ?? undefined,
        limit: 200,
      }),
    [location, counterparty, person, item, direction, state, from],
  );

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const results = transfers.data ?? [];
  const total = results.reduce(
    (sum, transfer) => sum + transfer.lines.reduce((n, line) => n + line.effective_quantity, 0),
    0,
  );

  return (
    <Screen title="Activity" back="/admin">
      <SectionLabel>Filter</SectionLabel>
      <div className="card">
        <SelectField label="Location" value={location} onChange={(e) => set('location', e.target.value)}>
          <option value="">Any location</option>
          {(locations.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="With"
          value={counterparty}
          onChange={(e) => set('counterparty', e.target.value)}
        >
          <option value="">Any other location</option>
          {(locations.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </SelectField>
        <SelectField label="Person" value={person} onChange={(e) => set('person', e.target.value)}>
          <option value="">Anyone</option>
          {(users.data ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name}
            </option>
          ))}
        </SelectField>
        <SelectField label="Item" value={item} onChange={(e) => set('item', e.target.value)}>
          <option value="">Any item</option>
          {(items.data ?? []).map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Direction"
          value={direction}
          onChange={(e) => set('direction', e.target.value)}
        >
          <option value="">Both directions</option>
          <option value="take">Taken</option>
          <option value="give">Given</option>
        </SelectField>
        <SelectField label="State" value={state} onChange={(e) => set('state', e.target.value)}>
          <option value="">Confirmed or not</option>
          <option value="confirmed">Confirmed</option>
          <option value="unconfirmed">Not yet confirmed</option>
        </SelectField>
        <SelectField
          label="When"
          value={range}
          onChange={(e) => setRange(e.target.value as 'month' | 'week' | 'all')}
        >
          <option value="week">This week</option>
          <option value="month">This month</option>
          <option value="all">All time</option>
        </SelectField>
      </div>

      <SectionLabel>
        {results.length} transfer{results.length === 1 ? '' : 's'}
        {total > 0 ? ` · ${Math.round(total * 100) / 100} units` : ''}
      </SectionLabel>

      {transfers.loading && <Spinner />}
      {transfers.error && <Notice>{transfers.error}</Notice>}
      {!transfers.loading && results.length === 0 && <Empty>Nothing matches those filters.</Empty>}

      <div className="stack">
        {results.map((transfer) => (
          <TransferCard
            key={transfer.id}
            transfer={transfer}
            onOpen={() => navigate(`/transfer/${transfer.id}`)}
          />
        ))}
      </div>
    </Screen>
  );
}

function startOf(range: 'month' | 'week' | 'all'): string | null {
  const now = new Date();
  if (range === 'all') return null;
  if (range === 'month') return new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
  const day = now.getDay();
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((day + 6) % 7));
  return monday.toISOString();
}
