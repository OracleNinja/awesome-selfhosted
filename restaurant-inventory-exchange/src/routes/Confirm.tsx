import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Empty, Notice, Screen, Spinner } from '../ui';
import { TransferCard } from '../ui/TransferCard';
import { useAsync } from '../state/useAsync';
import { useSession } from '../state/session';
import * as api from '../lib/api';

/**
 * Everything waiting on this location. A "give" from somewhere else is waiting
 * for you to say it arrived; a "take" from your shelves is waiting for you to
 * say it left. Both are the same gesture, so they share one screen.
 */
export default function Confirm() {
  const navigate = useNavigate();
  const { profile } = useSession();
  const locationId = profile?.location_id ?? null;
  const waiting = useAsync(
    () => (locationId ? api.fetchAwaitingConfirmation(locationId) : Promise.resolve([])),
    [locationId],
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function confirm(id: string) {
    setBusy(id);
    setError(null);
    try {
      await api.confirmTransfer(id);
      waiting.reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const incoming = (waiting.data ?? []).filter((t) => t.to_location_id === locationId);
  const takenFromUs = (waiting.data ?? []).filter((t) => t.from_location_id === locationId);

  return (
    <Screen title="Receive / confirm" back="/">
      {waiting.loading && <Spinner />}
      {waiting.error && <Notice>{waiting.error}</Notice>}
      {error && <Notice>{error}</Notice>}

      {waiting.data && waiting.data.length === 0 && <Empty>Nothing is waiting on you.</Empty>}

      {incoming.length > 0 && <h2 className="section-label">Incoming</h2>}
      <div className="stack">
        {incoming.map((transfer) => (
          <TransferCard
            key={transfer.id}
            transfer={transfer}
            onOpen={() => navigate(`/transfer/${transfer.id}`)}
            footer={
              <div style={{ padding: '0 16px 16px' }}>
                <button
                  type="button"
                  className="btn"
                  disabled={busy !== null}
                  onClick={() => confirm(transfer.id)}
                >
                  {busy === transfer.id ? 'Confirming…' : 'Confirm received'}
                </button>
              </div>
            }
          />
        ))}
      </div>

      {takenFromUs.length > 0 && <h2 className="section-label">Taken from us</h2>}
      <div className="stack">
        {takenFromUs.map((transfer) => (
          <TransferCard
            key={transfer.id}
            transfer={transfer}
            onOpen={() => navigate(`/transfer/${transfer.id}`)}
            footer={
              <div style={{ padding: '0 16px 16px' }}>
                <button
                  type="button"
                  className="btn"
                  disabled={busy !== null}
                  onClick={() => confirm(transfer.id)}
                >
                  {busy === transfer.id ? 'Confirming…' : 'Yes, that left here'}
                </button>
              </div>
            }
          />
        ))}
      </div>
    </Screen>
  );
}
