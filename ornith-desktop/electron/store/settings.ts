import { readFileSync, renameSync, writeFileSync, openSync, fsyncSync, closeSync } from 'node:fs';
import path from 'node:path';
import { DEFAULT_SETTINGS, SETTINGS_BOUNDS } from '../../shared/defaults';
import type { Settings, ThemePreference } from '../../shared/types';

export interface SettingsStore {
  get(): Settings;
  update(patch: Partial<Settings>): Settings;
  /** Path of the backup written when a corrupt file was found, if any. */
  readonly corruptBackupPath: string | null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/**
 * Validates field by field, falling back to the default for each invalid field
 * individually. One bad value must not discard an otherwise good file.
 */
export function validateSettings(raw: unknown): Settings {
  const input = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>;
  const out: Settings = { ...DEFAULT_SETTINGS };

  if (typeof input.ollamaUrl === 'string' && /^https?:\/\/\S+$/.test(input.ollamaUrl.trim())) {
    out.ollamaUrl = input.ollamaUrl.trim().replace(/\/$/, '');
  }
  if (typeof input.model === 'string' && input.model.trim()) {
    out.model = input.model.trim();
  }
  if (isFiniteNumber(input.temperature)) {
    out.temperature = clamp(
      input.temperature,
      SETTINGS_BOUNDS.temperature.min,
      SETTINGS_BOUNDS.temperature.max,
    );
  }
  if (isFiniteNumber(input.topP)) {
    out.topP = clamp(input.topP, SETTINGS_BOUNDS.topP.min, SETTINGS_BOUNDS.topP.max);
  }
  if (isFiniteNumber(input.numCtx)) {
    out.numCtx = Math.floor(
      clamp(input.numCtx, SETTINGS_BOUNDS.numCtx.min, SETTINGS_BOUNDS.numCtx.max),
    );
  }
  if (typeof input.keepAlive === 'string' && input.keepAlive.trim()) {
    out.keepAlive = input.keepAlive.trim();
  }
  if (input.theme === 'dark' || input.theme === 'light' || input.theme === 'system') {
    out.theme = input.theme as ThemePreference;
  }
  if (typeof input.showThinkingByDefault === 'boolean') {
    out.showThinkingByDefault = input.showThinkingByDefault;
  }
  if (typeof input.sendOnEnter === 'boolean') {
    out.sendOnEnter = input.sendOnEnter;
  }

  return out;
}

/** Write to a temp file, fsync, then rename. rename is atomic on APFS and ext4. */
export function writeSettingsAtomic(filePath: string, settings: Settings): void {
  const tmp = `${filePath}.tmp`;
  const json = `${JSON.stringify(settings, null, 2)}\n`;

  writeFileSync(tmp, json, 'utf8');
  const fd = openSync(tmp, 'r+');
  try {
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(tmp, filePath);
}

export function createSettingsStore(
  filePath: string,
  onChange?: (settings: Settings) => void,
): SettingsStore {
  let corruptBackupPath: string | null = null;
  let current: Settings;

  try {
    const raw = readFileSync(filePath, 'utf8');
    const parsed: unknown = JSON.parse(raw);
    current = validateSettings(parsed);
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code !== 'ENOENT') {
      // Unreadable or malformed: keep the evidence, carry on with defaults.
      try {
        const backup = path.join(
          path.dirname(filePath),
          `settings.corrupt-${Date.now()}.json`,
        );
        renameSync(filePath, backup);
        corruptBackupPath = backup;
      } catch {
        // If even the rename fails, defaults are still the right outcome.
      }
    }
    current = { ...DEFAULT_SETTINGS };
  }

  return {
    get() {
      return { ...current };
    },

    update(patch) {
      current = validateSettings({ ...current, ...patch });
      try {
        writeSettingsAtomic(filePath, current);
      } catch {
        // Disk full or read-only: the in-memory value still applies this session.
      }
      onChange?.({ ...current });
      return { ...current };
    },

    get corruptBackupPath() {
      return corruptBackupPath;
    },
  };
}
