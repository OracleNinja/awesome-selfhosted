import { useEffect, useRef } from 'react';
import { SETTINGS_BOUNDS, DEFAULT_SETTINGS } from '../../shared/defaults';
import type { OllamaStatus, PublicSettings, Settings } from '../../shared/types';
import type { VoiceCapabilities } from '../../shared/voice';

interface Props {
  settings: PublicSettings;
  status: OllamaStatus | null;
  voice: VoiceCapabilities | null;
  onUpdate: (patch: Partial<Settings>) => void;
  onClose: () => void;
}

/** Changes apply immediately; there is no Save button to forget to press. */
export default function SettingsDialog({ settings, status, voice, onUpdate, onClose }: Props) {
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
            <span className="field-label">Connection mode</span>
            <select
              value={settings.mode}
              onChange={(e) => onUpdate({ mode: e.target.value as Settings['mode'], modeChosen: true })}
              data-testid="settings-mode"
            >
              <option value="local">Local — Ollama on this device</option>
              <option value="online">Online — cloud AI with web retrieval</option>
            </select>
            <span className="field-hint">
              {settings.mode === 'online'
                ? 'Conversations are sent to the configured gateway.'
                : 'Everything stays on this device.'}
            </span>
          </label>

          <label className="field">
            <span className="field-label">Online gateway URL</span>
            <input
              type="text"
              value={settings.gatewayUrl}
              onChange={(e) => onUpdate({ gatewayUrl: e.target.value })}
              data-testid="settings-gateway-url"
            />
            <span className="field-hint">Not secret. Example: http://localhost:8787</span>
          </label>

          <label className="field">
            <span className="field-label">
              Online API token{' '}
              <code data-testid="settings-token-state">
                {settings.gatewayTokenConfigured ? 'Configured' : 'Not configured'}
              </code>
            </span>
            <input
              type="password"
              placeholder={settings.gatewayTokenConfigured ? '••••••••  (replace to change)' : 'Paste gateway token'}
              onChange={(e) => onUpdate({ gatewayToken: e.target.value })}
              data-testid="settings-gateway-token"
            />
            <span className="field-hint">
              Write-only: the token is stored by the app and never sent back to this window.
            </span>
          </label>

          <label className="field field-inline">
            <input
              type="checkbox"
              checked={settings.webRetrieval}
              onChange={(e) => onUpdate({ webRetrieval: e.target.checked })}
            />
            <span>Allow web retrieval in Online mode</span>
          </label>

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

          <label className="field field-inline">
            <input
              type="checkbox"
              checked={settings.speakResponses}
              onChange={(e) => onUpdate({ speakResponses: e.target.checked })}
              data-testid="settings-speak"
            />
            <span>Speak every response aloud</span>
          </label>

          <label className="field">
            <span className="field-label">Voice</span>
            <select
              value={settings.voiceName}
              onChange={(e) => onUpdate({ voiceName: e.target.value })}
              disabled={!voice?.tts.available}
              data-testid="settings-voice"
            >
              <option value="">System default</option>
              {(voice?.tts.voices ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <span className="field-hint">
              {voice?.tts.available
                ? 'Replies to spoken prompts are always spoken.'
                : (voice?.tts.reason ?? 'Speech output is unavailable.')}
            </span>
          </label>

          <label className="field">
            <span className="field-label">
              Speech rate <code>{settings.speechRate}</code> wpm
            </span>
            <input
              type="range"
              min={SETTINGS_BOUNDS.speechRate.min}
              max={SETTINGS_BOUNDS.speechRate.max}
              step={5}
              value={settings.speechRate}
              onChange={(e) => onUpdate({ speechRate: Number(e.target.value) })}
              disabled={!voice?.tts.available}
            />
            <span className="field-hint">
              Dictation:{' '}
              {voice?.stt.available ? 'on-device speech recognition ready' : (voice?.stt.reason ?? 'unavailable')}
            </span>
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
