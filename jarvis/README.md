# JARVIS

A modular personal AI operating system.

JARVIS is not a model. It is the system *around* a model: identity, memory,
tools, permissions, routing, agents, approvals, audit history and interfaces.
The model is a replaceable component — swap NVIDIA for Anthropic, OpenAI or a
local GPU runtime by changing one line of configuration.

**Status: v0.1.** Everything documented here is implemented and tested. Where a
capability needs a credential JARVIS does not have, it says so plainly rather
than pretending — that behaviour is itself tested.

---

## Quick start

```bash
cd jarvis
npm install
cp .env.example .env        # then add your NVIDIA_API_KEY
npm run dev                 # server on :8787, UI on :5173
```

Open **http://localhost:5173**.

Without an API key JARVIS still runs: memory, tools, approvals, audit, agents
and the whole UI work. The conversation itself will tell you the model provider
is not configured, and how to fix it.

### Other commands

| Command | What it does |
|---|---|
| `npm run dev` | Server + web UI together, both watching |
| `npm test` | The full test suite (141 tests) |
| `npm run typecheck` | TypeScript across server, packages and web |
| `npm run build` | Production build: bundled server + static UI |
| `npm start` | Run the production build (serves the UI too) |
| `npm run verify` | typecheck → test → build, in one go |
| `npm run db:reset` | Delete the database and start clean |

---

## Connecting the NVIDIA API

1. Get a key from [build.nvidia.com](https://build.nvidia.com) (`nvapi-…`).
2. In `.env`:

```bash
MODEL_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-your-key-here
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=meta/llama-3.3-70b-instruct
```

3. Restart, then open **Settings → Test live connection**. It performs a real
   completion and reports the round-trip time, or the exact error.

Pick any model from the NVIDIA catalogue that supports tool calling —
`meta/llama-3.3-70b-instruct`, `nvidia/llama-3.1-nemotron-70b-instruct`,
`mistralai/mistral-large-2-instruct` and similar. A model without tool calling
will chat but cannot use tools or delegate.

**Self-hosted NIM / local GPU.** The same provider covers it: point
`NVIDIA_BASE_URL` at your NIM container (`http://localhost:8000/v1`). Endpoints
on localhost do not require an API key.

**Other providers.** `MODEL_PROVIDER=anthropic | openai | local`, each with its
own `*_API_KEY`, `*_BASE_URL` and `*_MODEL`. `local` is a working
OpenAI-compatible client with no default endpoint — point `LOCAL_BASE_URL` at
vLLM, llama.cpp, Ollama or TensorRT-LLM and it comes online.

**No key ever reaches the browser.** Keys are read server-side only; there is no
API route that returns one, and a test asserts that no key appears in any
response the UI can fetch.

---

## What v0.1 does

**Conversation.** Multi-turn chat with persistent history in SQLite.

**Memory.** Explicit long-term memory in seven types — `preference`, `fact`,
`goal`, `project`, `person`, `instruction`, `temporary` — each with confidence,
importance, source and tags. JARVIS decides when to save something; it does not
dump every message into memory. Retrieval is lexical relevance scoring, ranked
by importance, confidence and recency, injected into the system prompt.

**Tools.** 13 registered tools, each declaring a name, description, JSON Schema,
risk level and approval requirement:

| Tool | Risk | Approval |
|---|---|---|
| `current_time` | READ | — |
| `web_search` | READ | — |
| `memory_search` | READ | — |
| `file_read` · `file_list` | READ | — |
| `task_list` | READ | — |
| `memory_write` | WRITE | — |
| `task_create` · `task_update` | WRITE | — |
| `delegate_agent` | WRITE | — |
| `file_write` | WRITE | **yes** (tool opts in) |
| `file_delete` | DESTRUCTIVE | **yes** |
| `memory_forget` | DESTRUCTIVE | **yes** |

There is deliberately **no shell tool**. Arbitrary command execution would make
every other permission in the system decorative.

**Agents.** Four specialists with capability ceilings enforced in code:

| Agent | Purpose | Ceiling |
|---|---|---|
| **Scout** | Gather information, report findings | `READ`, read-only |
| **Operator** | Perform approved work | `DESTRUCTIVE` (approval-gated) |
| **Advisor** | Analyse and prioritise | `WRITE` |
| **Developer** | Software work | `WRITE`, no delete, no shell |

An agent's model never sees a tool it may not use — the list is filtered before
the prompt is built. See [`agents/`](agents/).

**Approvals.** Anything `EXTERNAL_ACTION` or `DESTRUCTIVE` stops and waits for a
human. The UI shows what JARVIS wants to do, the risk level and the full
arguments, with APPROVE and DENY. The model is told, in the tool result, that
the action has **not** run — so it reports it as pending instead of narrating a
success. Approvals expire, cannot be replayed, and cannot be bypassed by
anything the model says.

**Audit.** Every tool invocation is recorded — successes, failures, refusals,
denials and pending approvals alike — with timestamp, agent, tool, arguments,
approval state, result, error and duration. Credential-shaped values are
redacted before they are written.

**Voice.** Speech-to-text and text-to-speech behind provider interfaces.
Browser mode (Web Speech API, no key, nothing leaves the machine) is the
default; NVIDIA Riva / NIM endpoints are used when configured.

**Image / video / vision.** Real provider interfaces with real NVIDIA adapters.
When unconfigured they report `available: false` with the reason and every call
returns HTTP 503 — never a fabricated result.

**Mission control UI.** Conversation, Activity (live event stream + audit log),
Memory, Agents, Tools and Settings. Live activity over SSE. Responsive down to
a 390px phone.

---

## Architecture

```
apps/
  server/        HTTP API, auth, static hosting
  web/           React mission-control UI
packages/
  shared/        types, events, config, JSON Schema validation
  core/          orchestrator, tool executor, composition root
  memory/        SQLite schema, migrations, repositories
  agents/        agent definitions, charters, runner, delegation
  tools/         tool registry and the built-in tools
  providers/     ModelProvider + NVIDIA/Anthropic/OpenAI/local, media, search
  voice/         SpeechToText / TextToSpeech providers
  security/      permission policy, risk levels, path sandbox
agents/          per-agent charters (README + optional agent.json override)
docs/            architecture, security, development, production, testing, roadmap
tests/           141 tests across nine suites
```

No orchestration framework. The control flow is a plain loop in
[`packages/core/src/orchestrator.ts`](packages/core/src/orchestrator.ts) that a
human can read in one sitting — the property that matters most in a system
allowed to act on your behalf.

Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Using voice

**Browser mode (default, no configuration).**

- Click 🎙 in the composer and speak. Recognition happens in the browser via the
  Web Speech API; the transcript lands in the message box for you to review
  before sending.
- Tick **Speak replies** to have JARVIS read its answers aloud via
  `speechSynthesis`.
- Nothing is uploaded and no key is needed. Chrome and Edge support recognition;
  Firefox does not — the button is disabled there rather than silently failing.

**Server-side NVIDIA voice.**

```bash
STT_PROVIDER=nvidia
NVIDIA_STT_URL=https://your-riva-or-nim-endpoint/asr
NVIDIA_STT_MODEL=parakeet-ctc-1.1b

TTS_PROVIDER=nvidia
NVIDIA_TTS_URL=https://your-riva-or-nim-endpoint/tts
NVIDIA_TTS_MODEL=fastpitch-hifigan
NVIDIA_TTS_VOICE=English-US.Female-1
```

`POST /api/voice/transcribe` and `POST /api/voice/speak` then do the work
server-side. In browser mode those routes return **501** with an explanation
rather than pretending to transcribe.

---

## Adding a tool

One file, one line. The registry, permission policy, approval gate, audit log
and UI all pick it up from the declared risk level.

```ts
// packages/tools/src/builtin/weather.ts
import type { ToolDefinition } from '@jarvis/shared';

export function weatherTool(apiKey: string): ToolDefinition {
  return {
    name: 'weather_current',
    description: 'Current conditions for a location.',
    risk: 'READ',                 // READ | WRITE | EXTERNAL_ACTION | DESTRUCTIVE
    requiresApproval: false,      // may opt in; can never opt out of policy
    inputSchema: {
      type: 'object',
      properties: { location: { type: 'string', minLength: 2 } },
      required: ['location'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      if (!apiKey) {
        // Never invent a result. Say the capability is unavailable.
        return { ok: false, summary: 'Weather is not configured.', error: 'WEATHER_API_KEY is unset' };
      }
      const data = await fetchWeather(String(args.location), apiKey);
      return { ok: true, summary: `${data.tempC}°C in ${data.name}`, data };
    },
  };
}
```

Then add it to `createBuiltinTools()` in
[`packages/tools/src/index.ts`](packages/tools/src/index.ts), and grant it to
whichever agents should have it in
[`packages/agents/src/definitions.ts`](packages/agents/src/definitions.ts).

MCP servers plug in at the same seam: an MCP client that maps each remote tool
to a `ToolDefinition` gets approvals, permissions and audit for free. That is
the first item on the [v0.2 roadmap](docs/ROADMAP.md).

---

## Security

- **Two independent gates** on every tool call: agent capability (checked before
  the model is prompted) and human approval (checked at execution). Neither
  consults the model.
- **Filesystem sandbox** — file tools cannot leave `JARVIS_WORKSPACE_DIR`.
  Traversal, absolute paths and null bytes are rejected on the resolved path.
- **No shell.**
- **Secrets stay server-side**, and are redacted from audit rows and error
  responses.
- **API auth** — set `JARVIS_API_TOKEN` and every `/api` request needs
  `Authorization: Bearer …`, compared in constant time. With no token set the
  server binds to loopback only.

Details and threat model: [docs/SECURITY.md](docs/SECURITY.md).

---

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, orchestration loop, event model, providers |
| [SECURITY.md](docs/SECURITY.md) | Permission model, approvals, threat model |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, layout, adding things |
| [PRODUCTION.md](docs/PRODUCTION.md) | Deployment, hardening, backups |
| [TESTING.md](docs/TESTING.md) | What is tested and how to run it |
| [ROADMAP.md](docs/ROADMAP.md) | v0.2 extension points |

---

## Licence

Part of the `awesome-selfhosted` fork. See the repository's `LICENSE`.
