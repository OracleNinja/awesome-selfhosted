# Ornith Portable

Ornith Portable is Ornith Desktop running entirely from a drive: the app, the
inference runtime, the models, and every byte of conversation history live on
the drive and travel with it. Plug it into a machine that has never heard of
Ornith, launch it, and it works. Unplug it and the machine is as you found it.

It is a **mode of Ornith Desktop, not a fork**. The same build does both; what
changes is where it keeps its data and whether it starts its own model server.
[`SPEC.md`](./SPEC.md) remains authoritative for the architecture; this document
covers only what portability adds.

---

## The drive layout

```
<drive>/Ornith/                        the portable root
├── ornith-portable.json               marker + manifest — its presence IS portable mode
├── Ornith.command                     macOS launcher
├── Ornith.bat                         Windows launcher
├── ornith.sh                          Linux launcher
├── app/
│   ├── mac/Ornith Desktop.app
│   ├── win/Ornith Desktop.exe
│   └── linux/ornith-desktop
├── runtime/                           one slot per platform-arch
│   ├── darwin-arm64/{ollama, whisper-cli, piper}
│   ├── darwin-x64/…
│   ├── win32-x64/{ollama.exe, whisper-cli.exe, piper.exe}
│   ├── win32-arm64/…
│   ├── linux-x64/…
│   └── linux-arm64/…
├── models/                            OLLAMA_MODELS points here
│   ├── blobs/
│   └── manifests/
├── voice/
│   ├── whisper/ggml-base.en.bin       speech-to-text model
│   └── piper/en_US-*.onnx(.json)      text-to-speech voice
└── data/                              userData: everything the app writes
    ├── ornith.db
    ├── settings.json
    ├── logs/main.log
    ├── runtime-home/                  HOME for the bundled server
    └── runtime-tmp/                   OLLAMA_TMPDIR
```

Every path is derived from the single root by pure functions in
[`shared/portable.ts`](./shared/portable.ts), so the layout has exactly one
definition that the app, the provisioning script, and the tests all share.

### Why a marker file

Ornith does **not** try to detect whether it is on removable media. That is not
reliably knowable across operating systems, and it is the wrong question
anyway. The right question is *"did someone deliberately provision this tree as
a portable install?"* — and a file at the root answers that exactly, everywhere,
including for a plain folder on an internal disk. That is how the layout gets
developed and tested, and it is why a portable drive is just a directory.

The marker doubles as the manifest:

```json
{
  "layoutVersion": 1,
  "label": "Ornith Portable",
  "directories": { "data": "data", "models": "models", "runtime": "runtime", "app": "app" }
}
```

It is treated as **untrusted input**, because it is data on removable media: a
directory name containing a separator, a `..`, a drive letter, or a control
character is rejected and the default used instead. A `layoutVersion` newer than
the running build understands makes the app refuse portable mode outright and
say so, rather than write a v1 database into a v2 tree.

---

## How a launch decides where its data goes

In order, first match wins:

| # | Signal | Effect |
|---|---|---|
| 1 | `ORNITH_USER_DATA` | Explicit profile directory. The E2E test hook; wins over everything. |
| 2 | `ORNITH_PORTABLE=0` | Force installed mode. The escape hatch for a drive whose marker cannot be removed. |
| 3 | `ORNITH_PORTABLE_ROOT` | Explicit root. What the launcher scripts set. |
| 4 | A marker above the executable | The normal path: double-click the app on the drive and it finds its own tree. |
| 5 | Nothing | Ordinary installed app, data in the OS's application-support directory. |

The walk in (4) climbs from the executable's directory, because a packaged
macOS app sits several levels below the root
(`<root>/app/mac/Ornith Desktop.app/Contents/MacOS/`). It is bounded at eight
levels so an app run from anywhere else never wanders up and adopts a stray
file.

This decision happens before anything opens the database or the settings file,
because the whole point is that those land on the drive.

### When the drive cannot be written to

A write-protected or read-only-mounted drive is an ordinary state for removable
media, so it gets a plain error at startup naming the drive, rather than a
SQLite failure later. `mkdir -p` succeeds against an existing tree even on a
read-only mount, so the check is an actual probe write, not a permission-bit
test.

---

## The inference runtime

An installed Ornith connects to the Ollama already on the machine. A portable
Ornith cannot assume there is one, so the drive carries a binary and the app
supervises it. Two rules govern this:

**It never fights an Ollama that is already running.** If something answers
`/api/version` at the configured host, that is the user's own server with the
user's own models: Ornith uses it and starts nothing. Spawning a second server
would either collide on the port or silently shadow their model store, and
[`SPEC.md` §19](./SPEC.md) ground rule 1 says the user's models are not ours to
touch.

**Everything the child writes lands on the drive.** `OLLAMA_MODELS` redirects
the model store, and `HOME` (plus `USERPROFILE` on Windows) is redirected too,
because Ollama writes a keypair and other state under `$HOME/.ollama` regardless
of `OLLAMA_MODELS`. Without that second redirect a "portable" app still litters
the host. `OLLAMA_TMPDIR` follows the drive for the same reason.

The server is bound to `127.0.0.1` on a port found by binding, not by assuming:
the port from the configured Ollama URL is preferred, and if something else
holds it the supervisor moves to an ephemeral one. That choice is **not** written
back to settings — an ephemeral port must not become permanent configuration on
the drive. So while a bundled server is running it is the host, whatever the
Ollama URL in Settings says; that setting is what the *next* launch probes and
prefers. The status-bar badge's tooltip names the host actually serving. On quit
the server gets `SIGTERM`, then `SIGKILL` after five seconds, so nothing is left
holding the drive open and blocking an eject.

The status bar reports which of the four states applies: `starting` (a cold
start off USB takes tens of seconds and is not the same as failure), `bundled`,
`external`, or `unavailable` with the reason.

---

## Voice

Both halves of the voice layer dispatch across two engines, preferring
whichever can actually run rather than branching on the platform:

| | On macOS | On Windows and Linux |
|---|---|---|
| **Speech-to-text** | the system Speech framework, via a small Swift helper, with `requiresOnDeviceRecognition` | `whisper-cli` and a GGML model, both off the drive |
| **Text-to-speech** | `/usr/bin/say` | Piper and a voice, both off the drive |

Preference is by availability, not by platform: a drive carrying whisper still
dictates on a Mac whose Swift helper was never compiled — which is every
packaged portable build, since compiling it needs Xcode.

Neither engine sends anything anywhere. Audio is captured in the renderer,
crosses IPC as WAV bytes, and is transcribed by a local process; nothing
touches the network. The Web Speech API is deliberately unused, because
Chromium's `webkitSpeechRecognition` streams microphone audio to Google's
servers while looking like a browser API.

**Where the audio comes out** differs, and it is the one place the two paths
are not symmetrical. `say` talks to the speakers itself, so speech begins on
the first word and stopping it is a signal. Piper only writes a WAV file, so
the bytes go to the renderer and Web Audio plays them. Decoding an
`ArrayBuffer` needs no blob or data URL, so the strict CSP (`default-src
'self'`, `connect-src 'none'`) is untouched — an `<audio src>` would have
forced `media-src blob:` open.

Voice is optional. A drive with no speech binaries is a working chat client
whose microphone button is disabled with a reason that names the missing file.

**The Piper and whisper.cpp command-line contracts here are written from their
documented interfaces and exercised against stand-in binaries, not against real
ones** — no such binary was available in the environment this was built in. The
argument construction is a pure, unit-tested function in each engine
(`whisperArgs`, `piperArgs`), so if a real binary disagrees, one function is
the fix.

---

## Capacity: what fits on 128 GB

Formatted capacity is about 119 GiB; the numbers below are approximate and
depend on quantisation.

| Component | Size | Notes |
|---|---|---|
| Electron app × 3 platforms | ~1.5 GB | ~500 MB unpacked each; carry only the platforms you need |
| Ollama runtime × 4 slots | ~1.5 GB | Binaries plus their GPU/accelerator libraries |
| Ornith 1.0 9B (Q4_K_M) | ~5.5 GB | The default model |
| Ornith 1.0 9B (Q8_0) | ~9.5 GB | If you want the higher-fidelity weights instead |
| whisper.cpp model | ~150 MB | `ggml-base.en`; `tiny.en` is ~75 MB, `small.en` ~500 MB |
| Piper voice | ~65 MB | Per voice, medium quality, including its config |
| Conversation history | < 100 MB | SQLite; text only, and text is small |
| Logs | ≤ 15 MB | Rotated at 5 MB, three files |
| **Working total** | **~9 GB** | One platform, one model, voice included |
| **Everything** | **~21 GB** | All platforms, all runtimes, both quantisations |

The practical answer is that a 128 GB drive is not close to full with one
model — it is sized for **several** models. Roughly 100 GB of headroom leaves
room for a 70B model at Q4 (~40 GB) alongside the default, or a dozen smaller
specialised models.

Two thresholds matter while running, because a full drive stops conversations
from saving:

- **under 5 GB free** — the badge turns amber
- **under 1 GB free**, or a read-only drive — the badge turns red

USB 3.x flash media is the real constraint, not capacity. First load of a 9B
model off a slow stick can take a minute; after that the model is resident in
RAM and inference speed is the host's, not the drive's. A USB-C SSD is
dramatically better than a thumb drive here, and worth the difference.

---

## Building a drive

```bash
# 1. Build the app for the platform you are on.
npm run package:portable          # → release/<platform>-unpacked/

# 2. Lay out the drive.
npm run provision -- --dest /Volumes/ORNITH/Ornith --label "My drive"

# 3. Optionally copy an existing model store across (copies; never modifies the source).
npm run provision -- --dest /Volumes/ORNITH/Ornith --copy-models ~/.ollama/models
```

`provision-portable.mjs` creates the tree, writes the marker, and drops the
launcher scripts. It deliberately **downloads nothing** — Ollama binaries and
model weights are large, licensed, and the user's business — and prints exactly
which files are still missing and where they go.

It never moves or modifies a source model store, and it leaves an existing
`data/` untouched, so re-running it against a drive already in use is safe.

### Filling in the runtime slots

Each platform slot holds up to three binaries, and the provisioning script
prints the exact path for every one it cannot find:

| Binary | Needed for | Releases |
|---|---|---|
| `ollama` | chat — required | <https://github.com/ollama/ollama/releases> |
| `whisper-cli` | dictation off macOS — optional | <https://github.com/ggml-org/whisper.cpp/releases> |
| `piper` | spoken replies off macOS — optional | <https://github.com/rhasspy/piper/releases> |

Windows binaries take a `.exe` suffix. On macOS and Linux each must be
executable (`chmod +x`). Models go in `voice/whisper/` (a `ggml-*.bin`) and
`voice/piper/` (an `.onnx` **and** its `.onnx.json` — a voice without its
config cannot be loaded, so it is not offered).

### Cross-platform builds

`electron-builder --dir` builds for the machine it runs on. A drive that serves
macOS, Windows, and Linux therefore needs the build step run once per platform
(or in CI), with each result copied into `app/mac`, `app/win`, `app/linux`.
**This has been exercised on Linux only**; the macOS and Windows packaging
targets are configured but unverified.

### Filesystem choice

exFAT is the only filesystem all three OSes read and write out of the box, so it
is the default recommendation. Two caveats:

- exFAT has no POSIX permission bits, so the executable bit on the Ollama binary
  does not survive a copy through it. On macOS and Linux, `chmod +x` after
  copying — or keep a per-platform drive on APFS/ext4/NTFS.
- SQLite runs in WAL mode. This is fine on exFAT for a single machine at a time,
  which is the portable case. Never open the same drive from two machines at
  once over a network share.

---

## Ejecting safely

Quit Ornith before unplugging. Quitting signals the bundled server, which is
what releases the model files; pulling the drive with the server still running
risks a torn write to the SQLite database, which WAL will recover from but which
is worth not doing.

---

## What is not portable

Stated plainly, because a portability claim is only useful if its edges are
known:

- **Voice needs binaries the drive carries** away from macOS, as described
  above. A drive provisioned without them still chats; it just cannot listen
  or speak, and says which file is missing.
- **The Piper and whisper.cpp CLI contracts are unverified against real
  binaries** — see the Voice section. Everything around them is tested.
- **Chromium may still write outside the drive.** Electron's `userData` is
  redirected, which covers the app's own storage and cache, but crash reporting
  and some OS-level integrations use platform paths this app does not control.
  The claim is that *Ornith's* data is on the drive — not that the process makes
  literally zero writes to the host.
- **Online mode still reaches the network** when explicitly enabled. Portable
  mode changes where data lives, not the privacy posture; local mode remains the
  default and is still the only mode that sends nothing anywhere.

---

## Tests

| Suite | Covers |
|---|---|
| `tests/unit/portable-layout.test.ts` | Path derivation, manifest validation, capacity thresholds, byte formatting |
| `tests/unit/portable-detect.test.ts` | The marker walk, resolution order, layout-version refusal, volume probing |
| `tests/integration/runtime-supervisor.test.ts` | Reuse vs. spawn, drive-scoped environment, port selection, shutdown escalation |
| `tests/e2e/portable.spec.ts` | The real app on a real tree: data location, restart persistence, a genuinely spawned server, and that an installed launch is unaffected |
| `tests/unit/whisper.test.ts`, `tests/unit/piper.test.ts` | Engine discovery, argument construction, transcript cleaning, voice resolution and its traversal rejections, and failure surfacing |
| `tests/e2e/voice.spec.ts` | The real app dictating and speaking off a provisioned drive, through spawned stand-in binaries and real Web Audio playback |

The E2E suite spawns a stand-in for the Ollama binary from the drive and asserts
on the environment that process actually received, so "everything the child
writes lands on the drive" is checked against a real spawn rather than assumed.
