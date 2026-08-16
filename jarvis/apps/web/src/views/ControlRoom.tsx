/**
 * The Control Room.
 *
 * A layout over the runtime adapter: core in the centre, agents and providers
 * to the sides, approvals and activity where they can be seen. It holds no
 * runtime state of its own — every panel below reads from the store, which is
 * fed exclusively by the runtime's own state snapshots and event stream.
 */
import {
  AgentGrid,
  ApprovalPanel,
  MemoryPanel,
  ProviderPanel,
  RuntimeEventStream,
  ScoutIntelligence,
  TelemetryPanel,
  ToolActivityPanel,
} from '../components/ControlPanels';
import { CoreSphere } from '../components/CoreSphere';
import { VoiceBar } from '../components/VoiceBar';
import { useRuntime, useRuntimeClient } from '../runtime/react';

export function ControlRoom({
  conversationId,
  onConversation,
}: {
  conversationId: string | null;
  onConversation: (id: string) => void;
}) {
  const client = useRuntimeClient();
  const connection = useRuntime((state) => state.connection);
  const detail = useRuntime((state) => state.connectionDetail);
  const lastError = useRuntime(
    (state) => state.activity?.lastError ?? null,
    (a, b) => a?.at === b?.at,
  );
  const charterErrors = useRuntime(
    (state) => state.snapshot?.charterErrors ?? [],
    (a, b) => a.length === b.length,
  );

  const offline = connection === 'offline' || connection === 'unauthorized';

  return (
    <div className="control-room" data-testid="control-room">
      {offline && (
        <div className="cr-banner offline" data-testid="offline-banner">
          <strong>{connection === 'unauthorized' ? 'UNAUTHORIZED' : 'RUNTIME OFFLINE'}</strong>
          <span>{detail}</span>
          <button className="btn btn-sm" onClick={() => void client.refreshState()}>
            Retry
          </button>
        </div>
      )}

      {!offline && lastError && (
        <div className="cr-banner degraded" data-testid="degraded-banner">
          <strong>{lastError.kind.toUpperCase()} ERROR</strong>
          <span>{lastError.summary}</span>
        </div>
      )}

      {charterErrors.length > 0 && (
        <div className="cr-banner degraded">
          <strong>AGENT CHARTER</strong>
          <span>{charterErrors.join('; ')}</span>
        </div>
      )}

      <div className="cr-grid">
        <aside className="cr-left">
          <ScoutIntelligence />
          <ProviderPanel />
          <TelemetryPanel />
        </aside>

        <section className="cr-centre">
          <CoreSphere size={300} />
          <AgentGrid />
        </section>

        <aside className="cr-right">
          <ApprovalPanel />
          <ToolActivityPanel />
          <MemoryPanel />
          <RuntimeEventStream limit={30} />
        </aside>
      </div>

      <VoiceBar conversationId={conversationId} onConversation={onConversation} />
    </div>
  );
}
