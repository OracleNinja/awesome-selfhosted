import { useEffect, useRef } from 'react';
import { SETTINGS_BOUNDS, DEFAULT_SETTINGS } from '../../shared/defaults';
import type { OllamaStatus, Settings } from '../../shared/types';

interface Props {
  settings: Settings;
  status: OllamaStatus | null;
  onUpdate: (patch: Partial<Settings>) => void;
  onClose: () => void;
}

/** Changes apply immediately; there is no Save button to forget to press. */
export default function SettingsDialog({ settings, status, onUpdate, onClose }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    dialogRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const models = status?.models ?? [];
  const modelOptions = models.includes(settings.model) ? models : [settings.model, ...models];

  return (
    <div className="modal-backdrop" onMouseDown={onClose} data-testid="settings-dialog">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        tabIndex={-1}
        ref={dialogRef}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2>Settings</h2>
          <button type="button" className="ghost-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="modal-body">
          <label className="field">
            <span className="field-label">Model</span>
            <select
              value={settings.model}
              onChange={(e) => onUpdate({ model: e.target.value })}
              data-testid="settings-model"
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <span className="field-hint">Default: {DEFAULT_SETTINGS.model}</span>
          </label>

          <label className="field">
            <span className="field-label">Ollama URL</span>
            <input
              type="text"
              value={settings.ollamaUrl}
              onChange={(e) => onUpdate({ ollamaUrl: e.target.value })}
              data-testid="settings-url"
            />
            <span className="field-hint">Default: {DEFAULT_SETTINGS.ollamaUrl}</span>
          </label>

          <label className="field">
            <span className="field-label">
              Temperature <code>{settings.temperature.toFixed(2)}</code>
            </span>
            <input
              type="range"
              min={SETTINGS_BOUNDS.temperature.min}
              max={SETTINGS_BOUNDS.temperature.max}
              step={0.05}
              value={settings.temperature}
              onChange={(e) => onUpdate({ temperature: Number(e.target.value) })}
              data-testid="settings-temperature"
            />
            <span className="field-hint">Default: {DEFAULT_SETTINGS.temperature}</span>
          </label>

          <label className="field">
            <span className="field-label">
              Top P <code>{settings.topP.toFixed(2)}</code>
            </span>
            <input
              type="range"
              min={SETTINGS_BOUNDS.topP.min}
              max={SETTINGS_BOUNDS.topP.max}
              step={0.01}
              value={settings.topP}
              onChange={(e) => onUpdate({ topP: Number(e.target.value) })}
            />
            <span className="field-hint">Default: {DEFAULT_SETTINGS.topP}</span>
          </label>

          <label className="field">
            <span className="field-label">Context window (tokens)</span>
            <input
              type="number"
              min={SETTINGS_BOUNDS.numCtx.min}
              max={SETTINGS_BOUNDS.numCtx.max}
              step={1024}
              value={settings.numCtx}
              onChange={(e) => onUpdate({ numCtx: Number(e.target.value) })}
              data-testid="settings-numctx"
            />
            <span className="field-hint">
              Default: {DEFAULT_SETTINGS.numCtx}. Larger windows use more memory.
            </span>
          </label>

          <label className="field">
            <span className="field-label">Keep model loaded for</span>
            <input
              type="text"
              value={settings.keepAlive}
              onChange={(e) => onUpdate({ keepAlive: e.target.value })}
            />
            <span className="field-hint">
              Default: {DEFAULT_SETTINGS.keepAlive}. Avoids a reload pause between messages.
            </span>
          </label>

          <label className="field">
            <span className="field-label">Appearance</span>
            <select
              value={settings.theme}
              onChange={(e) => onUpdate({ theme: e.target.value as Settings['theme'] })}
              data-testid="settings-theme"
            >
              <option value="system">System</option>
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </label>

          <label className="field field-inline">
            <input
              type="checkbox"
              checked={settings.showThinkingByDefault}
              onChange={(e) => onUpdate({ showThinkingByDefault: e.target.checked })}
            />
            <span>Always expand reasoning</span>
          </label>

          <label className="field field-inline">
            <input
              type="checkbox"
              checked={settings.sendOnEnter}
              onChange={(e) => onUpdate({ sendOnEnter: e.target.checked })}
            />
            <span>Enter sends the message</span>
          </label>
        </div>

        <footer className="modal-footer">
          <span className="field-hint">
            {status?.connected
              ? `Connected to ${status.host}${status.version ? ` (v${status.version})` : ''}`
              : 'Not connected'}
          </span>
        </footer>
      </div>
    </div>
  );
}
