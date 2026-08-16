/**
 * The top bar: clock, uptime, provider, voice and connection.
 *
 * Every field is real. The clock is the browser's; uptime, provider state and
 * voice mode come from the runtime; connection state is the client's own view
 * of the link. Where the runtime has not reported yet, the field shows a dash
 * rather than a zero, because "0s uptime" and "not connected yet" are different
 * facts.
 */
import { useEffect, useState } from 'react';
import { useRuntime } from '../runtime/react';
import { connectionLabel } from '../runtime/state';
import { formatUptime } from './ControlPanels';

export function TopBar() {
  const [clock, setClock] = useState(() => new Date());

  // The clock ticks in local component state — it is browser time, not runtime
  // state, and it must not churn the runtime store once a second.
  useEffect(() => {
    const timer = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const connection = useRuntime((state) => connectionLabel(state), (a, b) => a.label === b.label);
  const detail = useRuntime((state) => state.connectionDetail);
  const uptime = useRuntime((state) => state.telemetry?.uptimeSeconds ?? null);
  const activeModel = useRuntime((state) => state.snapshot?.activeModel ?? null);
  const voice = useRuntime(
    (state) => ({ phase: state.voice.phase, source: state.voice.source, mode: state.voice.sttMode }),
    (a, b) => a.phase === b.phase && a.source === b.source && a.mode === b.mode,
  );
  const version = useRuntime((state) => state.snapshot?.version ?? null);

  return (
    <header className="topbar-cr" data-testid="top-bar">
      <div className="tb-brand">
        <span className={`brand-dot ${connection.tone === 'ok' ? '' : 'offline'}`} />
        <span className="tb-name">J.A.R.V.I.S.</span>
        <span className="muted small mono">{version ?? '—'}</span>
      </div>

      <div className="tb-metrics">
        <TopBarField label="Time">
          <span className="mono" data-testid="tb-clock">
            {clock.toLocaleTimeString([], { hour12: false })}
          </span>
        </TopBarField>

        <TopBarField label="Uptime">
          <span className="mono" data-testid="tb-uptime">
            {uptime === null ? '—' : formatUptime(uptime)}
          </span>
        </TopBarField>

        <TopBarField label="Model">
          {activeModel ? (
            <span
              className={`chip ${activeModel.available ? 'ok' : 'off'}`}
              title={activeModel.reason ?? activeModel.model}
              data-testid="tb-provider"
            >
              {activeModel.provider.toUpperCase()}{' '}
              {activeModel.available ? 'AVAILABLE' : 'NOT CONFIGURED'}
            </span>
          ) : (
            <span className="chip off">—</span>
          )}
        </TopBarField>

        <TopBarField label="Voice">
          <span className="chip" data-testid="tb-voice">
            {voice.mode.toUpperCase()} · {voice.phase.toUpperCase()}
          </span>
        </TopBarField>

        <TopBarField label="Link">
          <span
            className={`chip ${connection.tone === 'ok' ? 'ok' : connection.tone === 'warn' ? 'EXTERNAL_ACTION' : 'DESTRUCTIVE'}`}
            title={detail}
            data-testid="tb-connection"
          >
            {connection.label}
          </span>
        </TopBarField>
      </div>
    </header>
  );
}

function TopBarField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="tb-field">
      <span className="tb-label">{label}</span>
      {children}
    </div>
  );
}
