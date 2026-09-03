import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Empty, List, Notice, Route, Row, Screen, SectionLabel, Spinner, Tag } from '../ui';
import { useAsync } from '../state/useAsync';
import { useSession } from '../state/session';
import { amount, fullWhen } from '../lib/format';
import * as api from '../lib/api';

/**
 * The permanent record. An admin can correct it, but only by adding to it:
 * the original numbers stay on the page next to the corrected ones.
 */
export default function TransferDetail() {
  const { id = '' } = useParams();
  const { profile } = useSession();
  const transfer = useAsync(() => api.fetchTransfer(id), [id]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isAdmin = profile?.role === 'admin';
  const data = transfer.data;

  async function correct(lineId: string, currentQuantity: number) {
    const raw = window.prompt('Corrected quantity', String(currentQuantity));
    if (raw === null) return;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      setError('Enter a number of zero or more.');
      return;
    }
    const reason = window.prompt('Why is it changing? This is kept with the record.');
    if (!reason || !reason.trim()) {
      setError('A correction needs a reason.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.adjustLine(lineId, value, reason.trim());
      transfer.reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function voidIt() {
    const reason = window.prompt('Why is this transfer being voided?');
    if (!reason || !reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.voidTransfer(id, reason.trim());
      transfer.reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen title="Transfer" back={() => window.history.back()}>
      {transfer.loading && <Spinner />}
      {transfer.error && <Notice>{transfer.error}</Notice>}
      {error && <Notice>{error}</Notice>}
      {!transfer.loading && !data && <Empty>That transfer is not available to you.</Empty>}

      {data && (
        <>
          <div style={{ margin: '16px 0 8px' }}>
            <Route from={data.from_location_name} to={data.to_location_name} />
          </div>
          <div className="row-inline" style={{ marginBottom: 16 }}>
            {data.voided ? (
              <Tag tone="void">Voided</Tag>
            ) : data.confirmed ? (
              <Tag tone="ok">Confirmed</Tag>
            ) : (
              <Tag tone="pending">Awaiting confirmation</Tag>
            )}
          </div>

          <SectionLabel>Items</SectionLabel>
          <List>
            {data.lines.map((line) => (
              <Row
                key={line.id}
                title={line.item_name}
                subtitle={
                  line.adjusted
                    ? `Originally ${amount(line.original_quantity, line.unit)}`
                    : undefined
                }
                value={amount(line.effective_quantity, line.unit)}
                onClick={
                  isAdmin && !data.voided
                    ? () => void correct(line.id, line.effective_quantity)
                    : undefined
                }
                trailing={isAdmin && !data.voided ? <span className="linkbtn">Correct</span> : undefined}
              />
            ))}
          </List>

          <SectionLabel>Record</SectionLabel>
          <List>
            <Row title={data.kind === 'take' ? 'Taken by' : 'Sent by'} value={data.recorded_by_name ?? '—'} />
            <Row title="Recorded" value={fullWhen(data.recorded_at)} />
            {data.confirmed && <Row title="Confirmed by" value={data.confirmed_by_name ?? '—'} />}
            {data.confirmed && data.confirmed_at && (
              <Row title="Confirmed" value={fullWhen(data.confirmed_at)} />
            )}
            {data.note && <Row title="Note" value={data.note} />}
          </List>

          {isAdmin && !data.voided && (
            <div style={{ marginTop: 24 }}>
              <button type="button" className="btn btn--danger" onClick={voidIt} disabled={busy}>
                Void this transfer
              </button>
              <p className="meta" style={{ marginTop: 8 }}>
                Voiding leaves the record in place and marks it as cancelled. Nothing is deleted.
              </p>
            </div>
          )}
        </>
      )}
    </Screen>
  );
}
