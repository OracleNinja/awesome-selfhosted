import type { AiMode, OllamaStatus } from '../../shared/types';

interface Props {
  status: OllamaStatus | null;
  onRetry: () => void;
  /** Which backend is answering. Always visible; never implied. */
  mode: AiMode;
  onlineConfigured: boolean;
  /** Lets a dead end offer the fix rather than just naming the problem. */
  onOpenSettings: () => void;
}

export default function StatusIndicator({
  status,
  onRetry,
  mode,
  onlineConfigured,
  onOpenSettings,
}: Props) {
  // Online mode has its own health story: Ollama being down is irrelevant to it.
  if (mode === 'online') {
    const ready = onlineConfigured;
    return (
      <div className="status-line" data-testid="status" data-state={ready ? 'online' : 'online-unconfigured'}>
        <span className={`status-dot is-${ready ? 'online' : 'model-missing'}`} aria-hidden="true" />
        <span className="status-text" data-testid="mode-label">
          {ready ? 'Online' : 'Online — not configured'}
        </span>
      </div>
    );
  }

  if (status === null) {
    return (
      <div className="status-line" data-testid="status" data-state="connecting">
        <span className="status-dot" aria-hidden="true" />
        <span>Connecting…</span>
      </div>
    );
  }

  // Three states, because "connected but the model is missing" is a distinct
  // problem from "nothing is listening" and needs a different fix.
  const state = !status.connected ? 'disconnected' : status.activeModelInstalled ? 'ready' : 'model-missing';

  if (state === 'ready') {
    return (
      <div className="status-line" data-testid="status" data-state={state}>
        <span className="status-dot is-ready" aria-hidden="true" />
        <span className="status-text" title={`${status.host} · ${status.activeModel}`} data-testid="mode-label">
          Local · {status.activeModel}
        </span>
      </div>
    );
  }

  // Both remaining states leave the app unusable, so each one names the thing
  // that would fix it and offers the button that does it. Diagnosing without
  // instructing is what left a new user staring at a dead app.
  if (state === 'model-missing') {
    const alternatives = status.models.length;

    return (
      <div className="status-line is-stacked" data-testid="status" data-state={state}>
        <div className="status-row">
          <span className="status-dot is-model-missing" aria-hidden="true" />
          <span className="status-text status-warning">{status.activeModel} isn’t installed</span>
        </div>

        <span className="status-help" data-testid="status-help">
          {alternatives > 0
            ? `${alternatives} other ${alternatives === 1 ? 'model is' : 'models are'} available here.`
            : `Add a model, then choose it here. Locally: ollama pull ${status.activeModel}`}
        </span>

        <button type="button" className="retry-button" onClick={onOpenSettings} data-testid="choose-model">
          {alternatives > 0 ? 'Choose a model' : 'Open settings'}
        </button>
      </div>
    );
  }

  return (
    <div className="status-line is-stacked" data-testid="status" data-state={state}>
      <div className="status-row">
        <span className="status-dot is-disconnected" aria-hidden="true" />
        <span className="status-text status-error" title={status.error?.detail}>
          {status.error?.message ?? 'Ollama unreachable'}
        </span>
      </div>

      <button type="button" className="retry-button" onClick={onRetry} data-testid="retry">
        Retry
      </button>
    </div>
  );
}
