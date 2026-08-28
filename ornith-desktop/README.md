# Ornith Desktop

A desktop chat application for local models served by [Ollama](https://ollama.com). No cloud API, no telemetry, no account — inference stays on your machine. Developed on macOS; the portable build runs on Windows and Linux too.

It also runs as **Ornith Portable**: the same build, carrying its app, runtime, models and history on a drive, so it works on a machine that has never had Ollama installed. See [`PORTABLE.md`](./PORTABLE.md).

Built to [`SPEC.md`](./SPEC.md), which remains the authoritative description of the architecture and the rationale behind it.

## Requirements

- Node.js 22 or newer
- [Ollama](https://ollama.com) running locally, with a model installed — except in portable
  mode, where the drive carries its own Ollama and starts it
- The default model is `ornith-en`; if it is absent the app falls back to the first installed model and says so in the status bar

## Getting started

```bash
npm install     # no native compilation step
npm run dev     # Vite + Electron, single Ctrl-C tears both down
```

## Commands

| Command | What it does |
|---|---|
| `npm run dev` | Development app with hot reload |
| `npm run typecheck` | `tsc --noEmit` for the renderer and the Electron/shared code |
| `npm run lint` | ESLint across the whole project |
| `npm test` | Unit + integration tests (Vitest) |
| `npm run test:coverage` | Same, with a V8 coverage report |
| `npm run test:e2e` | Playwright, launching the real Electron app |
| `npm run build` | Production build → `dist/` + `dist-electron/` |
| `npm start` | Build, then run the production bundle unpackaged |
| `npm run package` | `electron-builder --mac` → `release/*.app`, `*.dmg` |
| `npm run package:portable` | Unpacked build for this platform → `release/<platform>-unpacked/` |
| `npm run provision -- --dest <dir>` | Lay out an Ornith Portable drive at `<dir>` |

On Linux CI, run the E2E suite under a virtual display: `xvfb-run -a npm run test:e2e`.

## What it does

- Streaming responses with reasoning shown in a collapsible panel, separate from the answer
- Markdown with GFM tables, task lists, and syntax-highlighted code blocks with copy buttons
- Multiple conversations: create, switch, rename (double-click), delete — stored in SQLite and safe across a crash
- Settings for model, Ollama URL, temperature, top-p, context window, keep-alive, and theme
- Live connection status that distinguishes "Ollama is down" from "that model isn't installed"
- Portable mode: keeps everything on a drive and starts the Ollama the drive carries, reusing one that is already running rather than competing with it
- Dictation and spoken replies, entirely on-device: the macOS Speech framework and `say` where they exist, otherwise whisper.cpp and Piper carried on the drive

## Architecture in one paragraph

Three layers. The **main process** owns everything privileged: the Ollama HTTP client, the SQLite store, settings, and the window. The **preload** exposes a small typed surface over `contextBridge` and nothing else. The **renderer** is plain React with no Node, no filesystem, and a `connect-src 'none'` CSP — it cannot reach the network at all, so every byte to and from Ollama crosses an IPC boundary the main process controls. See `SPEC.md` §3 for why.

## Security posture

`contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, `webviewTag: false`, and a production CSP. Model output is rendered as Markdown with raw HTML disabled (`rehype-raw` is deliberately absent) and link schemes restricted to `http`/`https`/`mailto`.

Version 1 has **no agent capabilities**: `electron/agent/` contains the tool-registry and permission-broker interfaces, but the registry is empty and the broker denies every request. That is intentional — the interfaces exist so filesystem and command tools can be added later without redesigning the trust boundary. Read `SPEC.md` §11.5 and §15 before adding any tool.

## Data locations

Everything lives under Electron's `userData` directory — `~/Library/Application Support/Ornith Desktop/` on macOS, `%APPDATA%\Ornith Desktop\` on Windows, `~/.config/Ornith Desktop/` on Linux:

- `ornith.db` — conversations and messages (SQLite, WAL mode)
- `settings.json` — settings, written atomically
- `logs/main.log` — structured JSON logs, rotated at 5 MB. Conversation text is never logged.

In portable mode the same three live in `<drive>/Ornith/data/` instead, decided
before anything opens them. Which mode a launch is in, and how much room is left
on the drive, are shown in the status bar. See [`PORTABLE.md`](./PORTABLE.md).
