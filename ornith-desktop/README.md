# Ornith Desktop

A macOS desktop chat application for local models served by [Ollama](https://ollama.com). No cloud API, no telemetry — inference stays on the machine.

## Read this first

**[`SPEC.md`](./SPEC.md) is the authoritative engineering specification.** It defines the architecture, the Ollama integration, the security model, the data model, the test plan, and the phased implementation order for Version 1. Where this codebase and the specification disagree, the specification wins.

## Status of the code in this directory

This is a **reference prototype**, not the deliverable. It was built to validate the toolchain before the specification was written, and it is deliberately incomplete.

What it proves:

- The dual build pipeline works — Vite for the renderer, esbuild for main and preload.
- `node:sqlite` is available **unflagged inside Electron 39** (Node 22.22.1), so durable SQLite persistence needs no native module and no `electron-rebuild`.
- Main-process Ollama streaming over IPC works with `contextIsolation` on and zero renderer network access.
- React 19 + `react-markdown` + `rehype-highlight` builds clean (540 KB / 166 KB gzipped).

What it does **not** do — see `SPEC.md` §17 for the full defect list: no SQLite persistence (uses `localStorage`), no delta coalescing, no thinking-output handling, no settings, no menu bar, no tests, and `sandbox: false`.

Do not treat this code as a pattern to copy. Start from Phase 0 of the specification.

## Commands

```bash
npm install       # no native compilation step
npm run dev       # Vite + Electron, single Ctrl-C teardown
npm run typecheck # renderer and electron tsconfigs
npm run build     # dist/ (renderer) + dist-electron/ (main, preload)
npm start         # build, then run the production bundle
```

Requires Ollama running at `http://localhost:11434` with a local model. The prototype defaults to `ornith-en` and falls back to the first installed model if it is absent.
