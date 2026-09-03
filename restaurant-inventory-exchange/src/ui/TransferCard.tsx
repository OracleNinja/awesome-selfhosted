import { List, Route, Row, Tag } from './index';
import { amount, when } from '../lib/format';
import type { Transfer } from '../lib/types';

/** One transfer, rendered the same way everywhere it appears. */
export function TransferCard({
  transfer,
  onOpen,
  footer,
}: {
  transfer: Transfer;
  onOpen?: () => void;
  footer?: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="list__row list__row--static" style={{ display: 'block', paddingBottom: 8 }}>
        <Route from={transfer.from_location_name} to={transfer.to_location_name} />
      </div>
      <div className="list__row list__row--static" style={{ display: 'block', paddingTop: 0 }}>
        {transfer.lines.map((line) => (
          <div className="transfer-line" key={line.id}>
            <span className="transfer-line__qty">{amount(line.effective_quantity, line.unit)}</span>
            <span>{line.item_name}</span>
            {line.adjusted && <Tag tone="void">corrected</Tag>}
          </div>
        ))}
        <p className="meta" style={{ margin: '8px 0 0' }}>
          {transfer.kind === 'take' ? 'Taken by' : 'Sent by'} {transfer.recorded_by_name ?? 'someone'}
          {' · '}
          {when(transfer.recorded_at)}
        </p>
        <div className="row-inline" style={{ marginTop: 8 }}>
          {transfer.voided ? (
            <Tag tone="void">Voided</Tag>
          ) : transfer.confirmed ? (
            <Tag tone="ok">Confirmed</Tag>
          ) : (
            <Tag tone="pending">Awaiting confirmation</Tag>
          )}
        </div>
      </div>
      {footer}
      {onOpen && (
        <List>
          <Row title="Details" onClick={onOpen} />
        </List>
      )}
    </div>
  );
}
