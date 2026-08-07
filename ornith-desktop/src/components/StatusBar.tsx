import type { StatusResult } from '../types';

interface Props {
  status: StatusResult | null;
  model: string;
  onModelChange: (model: string) => void;
  onRetry: () => void;
}

export default function StatusBar({ status, model, onModelChange, onRetry }: Props) {
  const connected = status?.connected ?? false;
  const modelMissing = connected && status !== null && !status.models.includes(model);

  // Keep the current model selectable even if Ollama hasn't listed it yet.
  const options = status?.models.includes(model)
    ? status.models
    : [model, ...(status?.models ?? [])];

  return (
    <footer className="statusbar">
      <div className="status-left">
        <span
          className={`status-dot${connected ? ' is-connected' : ''}`}
          aria-hidden="true"
        />
        {status === null ? (
          <span>Connecting…</span>
        ) : connected ? (
          <span>
            Ollama {status.version ? `v${status.version}` : 'connected'} · {status.host}
          </span>
        ) : (
          <span className="status-error">{status.error ?? 'Ollama unreachable'}</span>
        )}

        {!connected && status !== null ? (
          <button className="ghost-button ghost-button-small" onClick={onRetry}>
            Retry
          </button>
        ) : null}
      </div>

      <div className="status-right">
        {modelMissing ? (
          <span className="status-warning" title={`${model} is not installed`}>
            not installed
          </span>
        ) : null}
        <label className="model-label" htmlFor="model-select">
          Model
        </label>
        <select
          id="model-select"
          className="model-select"
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          disabled={!connected}
        >
          {options.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
    </footer>
  );
}
