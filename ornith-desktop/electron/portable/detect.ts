/**
 * Deciding, at launch, whether this is a portable install and where its root
 * is. Runs before anything opens the database, because the answer determines
 * where the database lives.
 *
 * The filesystem calls are injectable so the decision logic can be tested
 * without building a drive; the default injection is the real `node:fs`.
 */
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import {
  DEFAULT_MANIFEST,
  MARKER_SEARCH_DEPTH,
  PORTABLE_MARKER,
  PORTABLE_LAYOUT_VERSION,
  isSupportedLayout,
  resolveLayout,
  validateManifest,
  type PortableLayout,
  type PortableManifest,
} from '../../shared/portable';

export interface PortableContext {
  portable: boolean;
  /** Null when running as an ordinary installed app. */
  root: string | null;
  manifest: PortableManifest;
  /** Null when not portable. */
  layout: PortableLayout | null;
  /** Set when a root was found but declined; surfaced in the log and the UI. */
  reason?: string;
}

export interface DetectFs {
  exists(target: string): boolean;
  readText(target: string): string;
}

export const realFs: DetectFs = {
  exists: (target) => existsSync(target),
  readText: (target) => readFileSync(target, 'utf8'),
};

export interface DetectOptions {
  /** Where the walk starts: the directory holding the running executable. */
  startDir: string;
  env?: NodeJS.ProcessEnv;
  fs?: DetectFs;
  depth?: number;
}

/**
 * Walks up from `startDir` looking for the marker. A packaged macOS app sits
 * several levels below the root it was provisioned into
 * (`<root>/app/mac/Ornith Portable.app/Contents/MacOS/`), so the walk has to
 * climb — but it is bounded, so an unprovisioned app never wanders up into a
 * user's home directory and adopts a stray file.
 */
export function findMarkerRoot(
  startDir: string,
  exists: (target: string) => boolean,
  depth: number = MARKER_SEARCH_DEPTH,
): string | null {
  let current = path.resolve(startDir);

  for (let level = 0; level <= depth; level += 1) {
    if (exists(path.join(current, PORTABLE_MARKER))) return current;

    const parent = path.dirname(current);
    if (parent === current) return null; // filesystem root
    current = parent;
  }

  return null;
}

function readManifest(root: string, fs: DetectFs): PortableManifest {
  const markerPath = path.join(root, PORTABLE_MARKER);
  if (!fs.exists(markerPath)) return { ...DEFAULT_MANIFEST };

  try {
    return validateManifest(JSON.parse(fs.readText(markerPath)) as unknown);
  } catch {
    // A truncated or hand-edited marker still identifies the root; the layout
    // defaults are what the provisioner writes anyway.
    return { ...DEFAULT_MANIFEST };
  }
}

const NOT_PORTABLE: PortableContext = {
  portable: false,
  root: null,
  manifest: DEFAULT_MANIFEST,
  layout: null,
};

/**
 * Resolution order, first match wins:
 *
 *   1. `ORNITH_PORTABLE=0`      — force installed mode. The escape hatch for a
 *                                 drive whose marker cannot be removed.
 *   2. `ORNITH_PORTABLE_ROOT`   — explicit root. What the launcher scripts set,
 *                                 and what the E2E suite uses.
 *   3. a marker above `startDir` — the normal path: double-click the app on the
 *                                 drive and it finds its own tree.
 */
export function detectPortable(options: DetectOptions): PortableContext {
  const env = options.env ?? process.env;
  const fs = options.fs ?? realFs;

  if (env.ORNITH_PORTABLE === '0') {
    return { ...NOT_PORTABLE, reason: 'Portable mode disabled by ORNITH_PORTABLE=0.' };
  }

  const explicit = env.ORNITH_PORTABLE_ROOT?.trim();
  const root = explicit
    ? path.resolve(explicit)
    : findMarkerRoot(options.startDir, fs.exists, options.depth);

  if (!root) return { ...NOT_PORTABLE };

  const manifest = readManifest(root, fs);

  if (!isSupportedLayout(manifest)) {
    // Refuse rather than guess: this drive was written by a newer Ornith, and
    // opening its data with this build is how a portable install corrupts.
    return {
      ...NOT_PORTABLE,
      reason:
        `The drive at ${root} uses portable layout v${manifest.layoutVersion}, ` +
        `but this build understands v${PORTABLE_LAYOUT_VERSION}. Update Ornith to use it.`,
    };
  }

  return { portable: true, root, manifest, layout: resolveLayout(root, manifest) };
}

/**
 * Creates the directories the app writes into. Called only after portable mode
 * is confirmed, and deliberately not for `runtime/` or `app/` — those are the
 * provisioner's to populate, and creating them empty here would make a
 * half-provisioned drive look complete.
 */
export function ensurePortableDirs(layout: PortableLayout): void {
  for (const dir of [
    layout.dataDir,
    layout.logsDir,
    layout.modelsDir,
    layout.runtimeHomeDir,
    layout.runtimeTmpDir,
  ]) {
    mkdirSync(dir, { recursive: true });
  }
}
