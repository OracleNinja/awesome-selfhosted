import { useMemo, useState } from 'react';
import { Empty, List, Notice, Row, Screen, SectionLabel, SelectField, Spinner } from '../../ui';
import { useAsync } from '../../state/useAsync';
import { amount } from '../../lib/format';
import * as api from '../../lib/api';
import type { Balance } from '../../lib/types';

/**
 * Net position, per pair of shops. No money: an item that went one way and
 * came back cancels out, and what is left is what is still owed.
 */
export default function Balances() {
  const balances = useAsync(() => api.fetchBalances(), []);
  const locations = useAsync(() => api.fetchLocations(true), []);
  const [focus, setFocus] = useState('');

  const pairs = useMemo(() => groupPairs(balances.data ?? [], focus), [balances.data, focus]);

  return (
    <Screen title="Balances" back="/admin">
      <p className="page-subtitle" style={{ marginTop: 16 }}>
        What one shop is still up on another, after everything that went back the other way.
      </p>

      <div className="card">
        <SelectField label="Focus on" value={focus} onChange={(e) => setFocus(e.target.value)}>
          <option value="">Every location</option>
          {(locations.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </SelectField>
      </div>

      {balances.loading && <Spinner />}
      {balances.error && <Notice>{balances.error}</Notice>}
      {!balances.loading && pairs.length === 0 && <Empty>Everything is even.</Empty>}

      {pairs.map(([key, rows]) => (
        <div key={key}>
          <SectionLabel>
            {rows[0]!.location_a_name} is up on {rows[0]!.location_b_name}
          </SectionLabel>
          <List>
            {rows.map((row) => (
              <Row
                key={row.item_id}
                title={row.item_name}
                value={<span className="mono">{amount(row.net_quantity, row.unit)}</span>}
              />
            ))}
          </List>
        </div>
      ))}
    </Screen>
  );
}

/**
 * The view returns both directions of every pair. Keep only the positive side,
 * so each pair is stated once as "A is up on B".
 */
function groupPairs(rows: Balance[], focus: string): Array<[string, Balance[]]> {
  const map = new Map<string, Balance[]>();
  for (const row of rows) {
    if (row.net_quantity <= 0) continue;
    if (focus && row.location_a !== focus && row.location_b !== focus) continue;
    const key = `${row.location_a}:${row.location_b}`;
    const list = map.get(key) ?? [];
    list.push(row);
    map.set(key, list);
  }
  return [...map.entries()].sort(([, a], [, b]) =>
    a[0]!.location_a_name.localeCompare(b[0]!.location_a_name),
  );
}
