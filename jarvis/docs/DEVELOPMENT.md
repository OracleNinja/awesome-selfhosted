# Development

## Setup

```bash
cd jarvis
npm install
cp .env.example .env
npm run dev
```

`npm run dev` starts both processes with prefixed output and shuts both down on
Ctrl-C:

- **server** — `tsx watch apps/server/src/index.ts` on `:8787`
- **web** — Vite dev server on `:5173`, proxying `/api` to the server

Open **http://localhost:5173**. Editing anything under `packages/` or
`apps/server/` restarts the server; editing `apps/web/` hot-reloads.

Requires Node 20.11+ (developed on 22). `better-sqlite3` ships prebuilt
binaries; a source build needs a C++ toolchain.

## Repository layout

```
jarvis/
├── apps/
│   ├── server/src/
│   │   ├── index.ts      entrypoint + startup report
│   │   ├── server.ts     request pipeline, static hosting
│   │   ├── routes.ts     the API
│   │   ├── http.ts       router, body parsing, SSE
│   │   └── auth.ts       bearer token / local mode
│   └── web/src/
│       ├── App.tsx       layout + state
│       ├── api.ts        typed client
│       ├── voice.ts      Web Speech API wrapper
│       ├── components/   ApprovalCard, ActivityFeed
│       └── views/        ConversationView, PanelViews
├── packages/
│   ├── shared/           types, events, config, JSON Schema validator
│   ├── core/             orchestrator, executor, composition root
│   ├── memory/           migrations, db, repositories
│   ├── agents/           definitions, charters, runner, delegation
│   ├── tools/            registry + built-ins
│   ├── providers/        model/media/search adapters
│   ├── voice/            STT/TTS providers
│   └── security/         policy, path sandbox
├── agents/               per-agent charters
├── docs/
├── scripts/              dev, build-server, db-reset
└── tests/
```

Workspaces are linked by npm; each package's `main` points at `src/index.ts`,
so `tsc`, `vitest`, `tsx` and `esbuild` all resolve `@jarvis/*` with no alias
configuration anywhere.

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | Server + UI together |
| `npm run dev:server` | Server only |
| `npm run dev:web` | UI only |
| `npm test` | Full suite |
| `npm run test:watch` | Watch mode |
| `npm run typecheck` | Server, packages and web |
| `npm run build` | Production build |
| `npm start` | Run the production build |
| `npm run verify` | typecheck → test → build |
| `npm run db:reset` | Delete the database |

## Adding a tool

1. Write it in `packages/tools/src/builtin/`, declaring `name`, `description`,
   `inputSchema`, `risk`, `requiresApproval` and `execute`.
2. Register it in `createBuiltinTools()` in `packages/tools/src/index.ts`.
3. Grant it to agents in `packages/agents/src/definitions.ts`.
4. Test it in `tests/tools.test.ts`.

The registry, policy, approval gate, audit log and Tools view need no changes —
they work from the declared risk level.

Rules that matter:

- Return `{ ok: false, summary, error }` for failure. Never throw for an
  expected condition, and never invent a plausible result.
- If a capability is unconfigured, say so in the error. The model relays that to
  the user instead of guessing.
- Keep `execute` free of permission logic. That belongs in the executor.

## Adding an agent

v0.1 ships four. To change one, edit `packages/agents/src/definitions.ts` or
drop an `agent.json` in `agents/<name>/` (see [`agents/README.md`](../agents/README.md)).

Adding a *new* agent means adding it to `AGENT_DEFINITIONS`; the charter loader
deliberately refuses to invent agents from disk, which is why it reports an
unknown directory as an error rather than registering it.

## Adding a provider

Implement `ModelProvider` in `packages/providers/src/`, add it to
`createModelProvider()`, and extend the `ModelProviderId` union in
`packages/shared/src/config.ts`. If the API is OpenAI-compatible, subclass
`OpenAICompatibleProvider` — that is all `NvidiaProvider` and `OpenAIProvider` are.

`isAvailable()` must be honest and `status().reason` must say what is missing.
That string is what the user reads in Settings.

## Conventions

- TypeScript everywhere, `strict` plus `noUncheckedIndexedAccess`.
- Explicit dependency injection; no globals or singletons.
- Comments explain *why*, not *what*.
- Every persistence path goes through `packages/memory`. No SQL outside it.
- Any ordering query on a timestamp needs a `rowid` tiebreaker.

## Debugging

- **"model provider is not configured"** — expected without a key. Set one, or
  point `NVIDIA_BASE_URL` at a local OpenAI-compatible server.
- **Live provider test** — Settings → Test live connection performs a real
  completion and shows the exact error on failure.
- **Tool refusals** — Activity view, audit table. Refusals are logged with the
  reason.
- **Database inspection** — `sqlite3 data/jarvis.db '.tables'`.
- **Reset state** — `npm run db:reset`.
