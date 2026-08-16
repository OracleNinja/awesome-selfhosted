import { useState } from 'react';
import type { Approval } from '../api';

/**
 * The human approval gate, as the user sees it.
 *
 * Shows exactly what JARVIS wants to do, at what risk level, with the full
 * structured arguments — no summarising away the details that matter.
 */
export function ApprovalCard({
  approval,
  onApprove,
  onDeny,
}: {
  approval: Approval;
  onApprove: (id: string) => Promise<void>;
  onDeny: (id: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState<'approve' | 'deny' | null>(null);

  const act = async (decision: 'approve' | 'deny') => {
    setBusy(decision);
    try {
      await (decision === 'approve' ? onApprove(approval.id) : onDeny(approval.id));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={`approval ${approval.risk}`}>
      <div className="approval-title">JARVIS wants to perform:</div>
      <div className="mono small" style={{ marginBottom: 8, wordBreak: 'break-word' }}>
        {approval.description}
      </div>

      <div className="row" style={{ gap: 6, marginBottom: 6 }}>
        <span className="muted small">Risk:</span>
        <span className={`chip ${approval.risk}`}>{approval.risk}</span>
        <span className="muted small">via {approval.agent}</span>
      </div>

      <div className="muted small">Arguments:</div>
      <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>

      <div className="approval-actions">
        <button
          className="btn btn-primary btn-sm"
          disabled={busy !== null}
          onClick={() => act('approve')}
        >
          {busy === 'approve' ? 'Executing…' : 'APPROVE'}
        </button>
        <button className="btn btn-danger btn-sm" disabled={busy !== null} onClick={() => act('deny')}>
          {busy === 'deny' ? 'Denying…' : 'DENY'}
        </button>
      </div>
      <div className="muted small" style={{ marginTop: 6 }}>
        Expires {new Date(approval.expiresAt).toLocaleTimeString()} · nothing runs until you decide.
      </div>
    </div>
  );
}
