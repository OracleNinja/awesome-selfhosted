/**
 * Ornith Portable — the on-drive layout.
 *
 * Every path the portable build touches is derived here, from one root, by
 * pure functions. Nothing in this file does I/O: it is imported by the main
 * process, by the provisioning script, and by tests, and each of those needs
 * to reason about the layout without a drive present.
 *
 * The layout these functions describe:
 *
 *   <root>/                        e.g. /Volumes/ORNITH/Ornith
 *   ├── ornith-portable.json       marker + manifest; its presence IS portable mode
 *   ├── app/                       packaged Electron builds, one dir per platform
 *   ├── runtime/<platform>-<arch>/ bundled Ollama binary
 *   ├── models/                    OLLAMA_MODELS points here (blobs/ + manifests/)
 *   └── data/                      userData: ornith.db, settings.json, logs/
 *
 * Why a marker file rather than asking "am I on a removable volume":
 * removability is neither portably knowable nor the right question. The right
 * question is "did someone provision this tree as a portable install", and a
 * file answers exactly that, on every OS — including for a plain folder on an
 * internal disk, which is how the layout gets developed and tested.
 */

/** File whose presence at the root marks a directory as a portable install. */
export const PORTABLE_MARKER = 'ornith-portable.json';

/** Bumped only when the on-drive layout changes shape. */
export const PORTABLE_LAYOUT_VERSION = 1;

/** How far up from the executable the marker is looked for. */
export const MARKER_SEARCH_DEPTH = 8;

/** Directory names under the root. Overridable per drive via the manifest. */
export const DEFAULT_DIRECTORIES = {
  data: 'data',
  models: 'models',
  runtime: 'runtime',
  app: 'app',
} as const;

export type PortableDirectoryKey = keyof typeof DEFAULT_DIRECTORIES;

/** The parsed `ornith-portable.json`. Every field is optional on disk. */
export interface PortableManifest {
  layoutVersion: number;
  /** Free-form label shown in the UI, e.g. the drive's name. */
  label: string;
  directories: Record<PortableDirectoryKey, string>;
}

export interface PortableLayout {
  root: string;
  markerPath: string;
  /** userData: the app writes here, and nowhere else on the host. */
  dataDir: string;
  logsDir: string;
  dbPath: string;
  settingsPath: string;
  /** OLLAMA_MODELS. */
  modelsDir: string;
  /** Parent of the per-platform runtime directories. */
  runtimeDir: string;
  appDir: string;
  /**
   * `HOME` for the bundled inference server. Ollama writes a keypair and
   * assorted state under `$HOME/.ollama` regardless of `OLLAMA_MODELS`, so
   * without this redirect a "portable" app still litters the host.
   */
  runtimeHomeDir: string;
  /** `OLLAMA_TMPDIR`: model-loading scratch space follows the drive too. */
  runtimeTmpDir: string;
}

/**
 * Where the inference server answering this session came from. `starting` is
 * its own state rather than an absence: a cold start off a USB drive takes
 * tens of seconds, and reporting that as "no runtime" for the whole of it
 * would be wrong in exactly the moment the user is watching.
 */
export type RuntimeSource = 'bundled' | 'external' | 'starting' | 'unavailable';

export interface VolumeStats {
  /** False when the drive is write-protected or mounted read-only. */
  writable: boolean;
  freeBytes: number;
  totalBytes: number;
}

export type CapacityLevel = 'ok' | 'low' | 'critical';

export interface PortableInfo {
  portable: boolean;
  /** Null when running as an ordinary installed app. */
  root: string | null;
  label: string;
  dataDir: string;
  modelsDir: string;
  runtime: {
    source: RuntimeSource;
    host: string;
    /** Populated when the runtime could not be started; shown to the user. */
    reason?: string;
  };
  volume: VolumeStats | null;
  capacity: CapacityLevel;
}

/* --------------------------------------------------------------- manifest */

export const DEFAULT_MANIFEST: PortableManifest = {
  layoutVersion: PORTABLE_LAYOUT_VERSION,
  label: 'Ornith Portable',
  directories: { ...DEFAULT_DIRECTORIES },
};

function hasControlCharacters(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/**
 * A directory name from the manifest must stay a single segment inside the
 * root. Anything carrying a separator, a drive letter, or a `..` is rejected
 * and the default used instead: the manifest is data on a removable drive, so
 * it is untrusted input, not configuration we wrote.
 */
export function isSafeSegment(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= 64 &&
    !/[/\\]/.test(value) &&
    value !== '.' &&
    value !== '..' &&
    !/^[A-Za-z]:/.test(value) &&
    // Control characters would produce paths no tool can name.
    !hasControlCharacters(value)
  );
}

/** Field-by-field validation: one bad entry must not discard the rest. */
export function validateManifest(raw: unknown): PortableManifest {
  const input = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>;
  const out: PortableManifest = {
    layoutVersion: PORTABLE_LAYOUT_VERSION,
    label: DEFAULT_MANIFEST.label,
    directories: { ...DEFAULT_DIRECTORIES },
  };

  if (typeof input.layoutVersion === 'number' && Number.isInteger(input.layoutVersion)) {
    out.layoutVersion = input.layoutVersion;
  }
  if (typeof input.label === 'string' && input.label.trim()) {
    out.label = input.label.trim().slice(0, 64);
  }

  const dirs = (
    typeof input.directories === 'object' && input.directories !== null ? input.directories : {}
  ) as Record<string, unknown>;

  for (const key of Object.keys(DEFAULT_DIRECTORIES) as PortableDirectoryKey[]) {
    const candidate = dirs[key];
    if (isSafeSegment(candidate)) out.directories[key] = candidate;
  }

  return out;
}

/**
 * A layout newer than this build understands is refused rather than guessed
 * at: writing a v1 database into a v2 tree is how a portable install corrupts.
 */
export function isSupportedLayout(manifest: PortableManifest): boolean {
  return manifest.layoutVersion <= PORTABLE_LAYOUT_VERSION;
}

/* ----------------------------------------------------------------- layout */

/**
 * Joins a root with known-safe segments. Node's `path` is deliberately not
 * imported: this module sits in `shared/`, which the renderer's type graph
 * includes, and that graph has no node types. Windows roots keep backslashes;
 * everything else is POSIX.
 */
function join(root: string, ...segments: string[]): string {
  const base = root.replace(/[/\\]+$/, '');
  const tail = segments.filter((segment) => segment && segment !== '.');
  if (tail.length === 0) return base;

  const windows = /^[A-Za-z]:/.test(base) || (base.includes('\\') && !base.includes('/'));
  const separator = windows ? '\\' : '/';
  return [base, ...tail].join(separator);
}

export function resolveLayout(
  root: string,
  manifest: PortableManifest = DEFAULT_MANIFEST,
): PortableLayout {
  const dirs = manifest.directories;
  const dataDir = join(root, dirs.data);

  return {
    root,
    markerPath: join(root, PORTABLE_MARKER),
    dataDir,
    logsDir: join(dataDir, 'logs'),
    dbPath: join(dataDir, 'ornith.db'),
    settingsPath: join(dataDir, 'settings.json'),
    modelsDir: join(root, dirs.models),
    runtimeDir: join(root, dirs.runtime),
    appDir: join(root, dirs.app),
    runtimeHomeDir: join(dataDir, 'runtime-home'),
    runtimeTmpDir: join(dataDir, 'runtime-tmp'),
  };
}

/* ---------------------------------------------------------------- runtime */

/**
 * Runtime directories are named `<platform>-<arch>` so that one drive can
 * carry every build it might be plugged into, and the right one is chosen at
 * launch with no configuration.
 */
export function runtimeSlug(platform: NodeJS.Platform, arch: string): string {
  return `${platform}-${arch}`;
}

export function runtimeBinaryName(platform: NodeJS.Platform): string {
  return platform === 'win32' ? 'ollama.exe' : 'ollama';
}

export function runtimeBinaryPath(
  layout: PortableLayout,
  platform: NodeJS.Platform,
  arch: string,
): string {
  return join(layout.runtimeDir, runtimeSlug(platform, arch), runtimeBinaryName(platform));
}

/** The slugs a provisioned drive should carry to cover mainstream hardware. */
export const SUPPORTED_RUNTIME_SLUGS = [
  'darwin-arm64',
  'darwin-x64',
  'win32-x64',
  'linux-x64',
] as const;

/* --------------------------------------------------------------- capacity */

/**
 * Thresholds in bytes. A stalled write on a full drive is the failure a USB
 * install hits first, and it hits it mid-conversation, so the warning has to
 * arrive while there is still room to act on it.
 */
export const CAPACITY_LOW_BYTES = 5 * 1024 ** 3;
export const CAPACITY_CRITICAL_BYTES = 1 * 1024 ** 3;

export function classifyCapacity(volume: VolumeStats | null): CapacityLevel {
  if (!volume) return 'ok';
  if (!volume.writable || volume.freeBytes < CAPACITY_CRITICAL_BYTES) return 'critical';
  if (volume.freeBytes < CAPACITY_LOW_BYTES) return 'low';
  return 'ok';
}

/** Compact, drive-sized units for the status bar: "42 GB", "980 MB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }

  const rounded = value >= 100 || unit === 0 ? Math.round(value) : Number(value.toFixed(1));
  return `${rounded} ${units[unit]}`;
}
