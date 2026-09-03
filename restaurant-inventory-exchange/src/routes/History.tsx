import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Empty, Notice, Screen, Spinner } from '../ui';
import { TransferCard } from '../ui/TransferCard';
import { useAsync } from '../state/useAsync';
import { useSession } from '../state/session';
import * as api from '../lib/api';

type Scope = 'location' | 'mine';

export default function History() {
  const navigate = useNavigate();
  const { profile } = useSession();
  const [scope, setScope] = useState<Scope>('location');
  const locationId = profile?.location_id ?? undefined;

  const transfers = useAsync(
    () =>
      api.fetchTransfers(
        scope === 'mine' ? { recordedBy: profile?.id } : { locationId },
      ),
    [scope, locationId, profile?.id],
  );

  const grouped = useMemo(() => groupByDay(transfers.data ?? []), [transfers.data]);

  return (
    <Screen title="History" back="/">
      <div className="segmented" role="group" aria-label="Which transfers to show">
        <button
          type="button"
          className="segmented__btn"
          aria-pressed={scope === 'location'}
          onClick={() => setScope('location')}
        >
          My location
        </button>
        <button
          type="button"
          className="segmented__btn"
          aria-pressed={scope === 'mine'}
          onClick={() => setScope('mine')}
        >
          Recorded by me
        </button>
      </div>

      {transfers.loading && <Spinner />}
      {transfers.error && <Notice>{transfers.error}</Notice>}
      {transfers.data && transfers.data.length === 0 && <Empty>Nothing recorded yet.</Empty>}

      {grouped.map(([day, list]) => (
        <div key={day}>
          <h2 className="section-label">{day}</h2>
          <div className="stack">
            {list.map((transfer) => (
              <TransferCard
                key={transfer.id}
                transfer={transfer}
                onOpen={() => navigate(`/transfer/${transfer.id}`)}
              />
            ))}
          </div>
        </div>
      ))}
    </Screen>
  );
}

const DAY_LABEL = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  month: 'short',
  day: 'numeric',
});

function groupByDay<T extends { recorded_at: string }>(rows: T[]): Array<[string, T[]]> {
  const today = new Date().toDateString();
  const map = new Map<string, T[]>();
  for (const row of rows) {
    const date = new Date(row.recorded_at);
    const label = date.toDateString() === today ? 'Today' : DAY_LABEL.format(date);
    const list = map.get(label) ?? [];
    list.push(row);
    map.set(label, list);
  }
  return [...map.entries()];
}
