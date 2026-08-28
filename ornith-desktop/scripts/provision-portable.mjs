/**
 * Lays out an Ornith Portable drive.
 *
 *   node scripts/provision-portable.mjs --dest /Volumes/ORNITH/Ornith
 *   node scripts/provision-portable.mjs --dest E:\\Ornith --app release/mac-arm64
 *   node scripts/provision-portable.mjs --dest /mnt/stick/Ornith --copy-models ~/.ollama/models
 *
 * What it does: creates the directory tree, writes the marker file that makes
 * the tree a portable install, and drops the launcher scripts. Optionally
 * copies a built app and an existing model store onto the drive.
 *
 * What it deliberately does NOT do:
 *
 *   - Download anything. Ollama binaries and model weights are large, licensed,
 *     and the user's business; the script tells you where to put them.
 *   - Move or modify a source model store. `--copy-models` copies, never moves,
 *     and never writes to the source — SPEC §19 ground rule 1.
 *   - Overwrite user data. An existing `data/` is left exactly as it is.
 */
import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  statfsSync,
  writeFileSync,
} from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_MANIFEST,
  PORTABLE_LAYOUT_VERSION,
  PORTABLE_MARKER,
  SUPPORTED_RUNTIME_SLUGS,
  formatBytes,
  resolveLayout,
  toolBinaryName,
} from '../shared/portable.ts';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/* -------------------------------------------------------------------- args */

function parseArgs(argv) {
  const args = { dest: null, app: null, copyModels: null, label: null, force: false };

  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];

    switch (flag) {
      case '--dest':
        args.dest = value;
        i += 1;
        break;
      case '--app':
        args.app = value;
        i += 1;
        break;
      case '--copy-models':
        args.copyModels = value;
        i += 1;
        break;
      case '--label':
        args.label = value;
        i += 1;
        break;
      case '--force':
        args.force = true;
        break;
      case '--help':
      case '-h':
        args.help = true;
        break;
      default:
        if (flag.startsWith('--')) throw new Error(`Unknown option: ${flag}`);
    }
  }

  return args;
}

const USAGE = `
Ornith Portable — drive provisioning

  node scripts/provision-portable.mjs --dest <dir> [options]

  --dest <dir>           Where the portable tree goes, e.g. /Volumes/ORNITH/Ornith
  --app <dir>            A built app directory to copy into <dest>/app/
  --copy-models <dir>    Copy an existing Ollama model store onto the drive.
                         Copies only; the source is never modified.
  --label <text>         Name shown in the app's status bar
  --force                Overwrite an existing marker written by another version
  -h, --help             This text
`;

/* ----------------------------------------------------------------- helpers */

function fail(message) {
  console.error(`[provision] ${message}`);
  process.exit(1);
}

function directorySize(dir) {
  let total = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const target = path.join(dir, entry.name);
    if (entry.isDirectory()) total += directorySize(target);
    else if (entry.isFile()) total += statSync(target).size;
  }
  return total;
}

function freeBytes(dir) {
  try {
    const stats = statfsSync(dir);
    return Number(stats.bavail) * Number(stats.bsize);
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------- steps */

function writeMarker(layout, label, force) {
  if (existsSync(layout.markerPath) && !force) {
    const existing = JSON.parse(readFileSync(layout.markerPath, 'utf8'));
    if (existing.layoutVersion !== PORTABLE_LAYOUT_VERSION) {
      fail(
        `${layout.markerPath} declares layout v${existing.layoutVersion}, this build writes ` +
          `v${PORTABLE_LAYOUT_VERSION}. Re-run with --force only if you know the data is compatible.`,
      );
    }
    // Same version: refresh the label but keep whatever else is there.
    const manifest = { ...existing, label: label ?? existing.label ?? DEFAULT_MANIFEST.label };
    writeFileSync(layout.markerPath, `${JSON.stringify(manifest, null, 2)}\n`);
    return manifest;
  }

  const manifest = {
    layoutVersion: PORTABLE_LAYOUT_VERSION,
    label: label ?? DEFAULT_MANIFEST.label,
    directories: { ...DEFAULT_MANIFEST.directories },
  };
  writeFileSync(layout.markerPath, `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

/**
 * The launchers exist so a double-click works even when the marker walk would
 * not — an app copied somewhere unexpected, or run from a shell whose working
 * directory is elsewhere. They pin the root explicitly.
 */
function writeLaunchers(root, layout) {
  const macCommand = `#!/bin/sh
# Ornith Portable — macOS launcher. Double-click, or run from a terminal.
here="$(cd "$(dirname "$0")" && pwd)"
export ORNITH_PORTABLE_ROOT="$here"
app="$here/app/mac/Ornith Desktop.app"
[ -d "$app" ] || { echo "No macOS build at $app" >&2; exit 1; }
open -a "$app" --args --portable
`;

  const linuxScript = `#!/bin/sh
# Ornith Portable — Linux launcher.
here="$(cd "$(dirname "$0")" && pwd)"
export ORNITH_PORTABLE_ROOT="$here"
exec "$here/app/linux/ornith-desktop" "$@"
`;

  const windowsScript = `@echo off
rem Ornith Portable - Windows launcher.
set "ORNITH_PORTABLE_ROOT=%~dp0"
start "" "%~dp0app\\win\\Ornith Desktop.exe" %*
`;

  const files = [
    ['Ornith.command', macCommand, 0o755],
    ['ornith.sh', linuxScript, 0o755],
    ['Ornith.bat', windowsScript, 0o644],
  ];

  for (const [name, body, mode] of files) {
    const target = path.join(root, name);
    writeFileSync(target, body, { mode });
  }

  return files.map(([name]) => path.join(layout.root, name));
}

function copyApp(source, layout) {
  const resolved = path.resolve(expandHome(source));
  if (!existsSync(resolved)) fail(`--app path does not exist: ${resolved}`);

  const destination = path.join(layout.appDir, path.basename(resolved));
  mkdirSync(layout.appDir, { recursive: true });
  cpSync(resolved, destination, { recursive: true, dereference: false });
  return destination;
}

/**
 * `~/x` and, on Windows, `~\x`. Windows sets USERPROFILE rather than HOME, and
 * without both checks a tilde survives into the path and `path.resolve` quietly
 * invents a directory literally named `~` under the working directory.
 */
export function expandHome(value) {
  const home = process.env.HOME ?? process.env.USERPROFILE;
  if (!home) return value;
  return value.replace(/^~(?=$|[/\\])/, home);
}

function copyModels(source, layout) {
  const resolved = path.resolve(expandHome(source));
  if (!existsSync(resolved)) fail(`--copy-models path does not exist: ${resolved}`);

  const size = directorySize(resolved);
  const available = freeBytes(layout.root);

  if (available !== null && size > available) {
    fail(
      `The model store is ${formatBytes(size)} but the drive has ${formatBytes(available)} free. ` +
        'Nothing was copied.',
    );
  }

  console.log(`[provision] Copying ${formatBytes(size)} of models — this takes a while…`);
  // Copy, never move: the source store stays exactly as it was.
  cpSync(resolved, layout.modelsDir, { recursive: true, dereference: false });
  return size;
}

/* -------------------------------------------------------------------- main */

function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help || !args.dest) {
    console.log(USAGE);
    process.exit(args.dest ? 0 : 1);
  }

  const root = path.resolve(args.dest);
  const layout = resolveLayout(root);

  mkdirSync(root, { recursive: true });
  for (const dir of [
    layout.dataDir,
    layout.logsDir,
    layout.modelsDir,
    layout.runtimeDir,
    layout.appDir,
    layout.runtimeHomeDir,
    layout.runtimeTmpDir,
    layout.voiceDir,
    layout.sttModelDir,
    layout.ttsVoiceDir,
  ]) {
    mkdirSync(dir, { recursive: true });
  }
  for (const slug of SUPPORTED_RUNTIME_SLUGS) {
    mkdirSync(path.join(layout.runtimeDir, slug), { recursive: true });
  }

  const manifest = writeMarker(layout, args.label, args.force);
  const launchers = writeLaunchers(root, layout);

  const copiedApp = args.app ? copyApp(args.app, layout) : null;
  const copiedModels = args.copyModels ? copyModels(args.copyModels, layout) : 0;

  /* ---- report ---- */

  const available = freeBytes(root);

  console.log(`\n[provision] Ornith Portable laid out at ${root}`);
  console.log(`  marker    ${PORTABLE_MARKER} (layout v${manifest.layoutVersion}, "${manifest.label}")`);
  console.log(`  data      ${layout.dataDir}`);
  console.log(`  models    ${layout.modelsDir}${copiedModels ? ` (+${formatBytes(copiedModels)})` : ''}`);
  console.log(`  runtime   ${layout.runtimeDir}`);
  console.log(`  voice     ${layout.voiceDir}`);
  if (copiedApp) console.log(`  app       ${copiedApp}`);
  for (const launcher of launchers) console.log(`  launcher  ${launcher}`);
  if (available !== null) console.log(`  free      ${formatBytes(available)}`);

  // Every tool the drive carries is found the same way, so report them the
  // same way: a slot per platform, named after the binary the app looks for.
  const platformOf = (slug) => (slug.startsWith('win32') ? 'win32' : 'linux');
  const slotFor = (slug, tool) =>
    path.join(layout.runtimeDir, slug, toolBinaryName(tool, platformOf(slug)));

  const missing = (tool) => SUPPORTED_RUNTIME_SLUGS.filter((slug) => !existsSync(slotFor(slug, tool)));

  const missingRuntimes = missing('ollama');
  const missingWhisper = missing('whisper');
  const missingPiper = missing('piper');

  const hasSttModel = readdirSync(layout.sttModelDir).some((n) => n.toLowerCase().endsWith('.bin'));
  const hasTtsVoice = readdirSync(layout.ttsVoiceDir).some((n) => n.toLowerCase().endsWith('.onnx'));

  console.log('\nStill to do by hand:');
  if (missingRuntimes.length > 0) {
    console.log(`  - Drop an Ollama binary into each runtime slot you need:`);
    for (const slug of missingRuntimes) console.log(`      ${slotFor(slug, 'ollama')}`);
    console.log('    Downloads: https://github.com/ollama/ollama/releases');
    console.log('    On macOS and Linux the binary must be executable: chmod +x <path>');
  }

  // Voice is optional — chat works without it — so these are listed after the
  // runtime and phrased as an addition, not a failure.
  if (missingWhisper.length > 0 || !hasSttModel) {
    console.log('  - Optional, for dictation away from macOS: whisper.cpp');
    for (const slug of missingWhisper) console.log(`      ${slotFor(slug, 'whisper')}`);
    if (!hasSttModel) console.log(`      a ggml-*.bin model in ${layout.sttModelDir}`);
    console.log('    Binaries: https://github.com/ggml-org/whisper.cpp/releases');
    console.log('    Models:   https://huggingface.co/ggerganov/whisper.cpp');
  }
  if (missingPiper.length > 0 || !hasTtsVoice) {
    console.log('  - Optional, for spoken replies away from macOS: Piper');
    for (const slug of missingPiper) console.log(`      ${slotFor(slug, 'piper')}`);
    if (!hasTtsVoice) {
      console.log(`      a voice (.onnx AND its .onnx.json) in ${layout.ttsVoiceDir}`);
    }
    console.log('    Binaries and voices: https://github.com/rhasspy/piper/releases');
  }

  if (!copiedModels) {
    console.log(`  - Put your models under ${layout.modelsDir} (blobs/ and manifests/),`);
    console.log('    or re-run with --copy-models ~/.ollama/models');
  }
  if (!copiedApp) {
    console.log(`  - Build the app and copy it into ${layout.appDir}`);
    console.log(`      npm run package:portable   (run from ${repoRoot})`);
  }
  console.log('');
}

main();
