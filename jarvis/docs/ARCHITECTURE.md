# Architecture

## The idea

JARVIS is an orchestration system, not a model wrapper. The model is the brain;
JARVIS owns everything around it — identity, memory, tools, permissions,
routing, agents, workflows, approvals, audit history and interfaces.

That split is the reason the codebase is laid out the way it is. Nothing in
`core`, `memory`, `tools`, `security` or `agents` knows what an NVIDIA endpoint
looks like. Providers adapt themselves to JARVIS's types, never the reverse.

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ apps/web          React mission control (browser)            │
├──────────────────────────────────────────────────────────────┤
│ apps/server       HTTP API · auth · SSE · static hosting     │
├──────────────────────────────────────────────────────────────┤
│ packages/core     Orchestrator · ToolExecutor · Jarvis root  │
├───────────────┬──────────────┬───────────────┬───────────────┤
│ agents        │ tools        │ security      │ memory        │
│ definitions   │ registry     │ policy        │ SQLite store  │
│ runner        │ built-ins    │ path sandbox  │ repositories  │
├───────────────┴──────────────┴───────────────┴───────────────┤
│ providers · voice     model / STT / TTS / image / video /    │
│                       vision / search adapters               │
├──────────────────────────────────────────────────────────────┤
│ shared            types · events · config · JSON Schema      │
└──────────────────────────────────────────────────────────────┘
```

Dependencies point downward only. `core` imports `agents`; `agents` never
imports `core` — the runner takes the executor as a structurally-typed port
(`ToolExecutorPort`), which keeps the arrow one-way and lets tests drive an
agent with a fake executor.

## The orchestration loop

[`packages/core/src/orchestrator.ts`](../packages/core/src/orchestrator.ts)

```
handleMessage(userId, conversationId, text)
  │
  ├─ ensure user + conversation           (creating either if needed)
  ├─ persist user message                 → USER_MESSAGE
  ├─ retrieve relevant memories           → MEMORY_READ
  ├─ build system prompt                  (persona + tools + memory)
  │
  ├─ loop, up to maxIterations:
  │    ├─ provider.chat(messages, tools)
  │    ├─ no tool calls?  → persist reply → MODEL_RESPONSE → done
  │    └─ tool calls?
  │         ├─ persist the assistant turn
  │         └─ for each call → executor.execute(...)
  │              └─ persist the tool result, feed it back
  │
  └─ return TurnResult { reply, messages, toolCalls, pendingApprovals, events }
```

It is a plain `while` loop on purpose. LangChain, AutoGen and CrewAI all hide
this control flow behind abstractions; in a system that is allowed to delete
your files, being able to read the whole decision path in one file is worth more
than the abstraction.

The model decides *what* to do — answer, retrieve, call a tool, write a memory,
delegate. JARVIS decides what is *allowed*, and that decision is made in code.

## The executor

[`packages/core/src/executor.ts`](../packages/core/src/executor.ts) is the single
choke point for every action. Six steps, none skippable:

1. **Resolve** — unknown tool → fail closed, audited.
2. **Permission** — may this agent use this tool? Refusal is audited as `denied`.
3. **Validate** — arguments against the tool's published JSON Schema.
4. **Approval** — `EXTERNAL_ACTION`/`DESTRUCTIVE` create a pending request and
   stop. The model gets an explicit "this has NOT been executed" message.
5. **Execute** — timed, exceptions caught and reported truthfully.
6. **Audit** — always, on every path above.

Because both agents and the orchestrator go through this one function, a
delegated Scout gets exactly the same enforcement as a direct user request.

## Delegation

Delegation is exposed to the model as a tool (`delegate_agent`) rather than
hidden inside a router. That is deliberate: it puts the decision to delegate in
the same event stream, audit log and approval path as everything else.

```
JARVIS ──delegate_agent(agent, task)──▶ AgentRunner
                                          │  builds the agent's own system prompt
                                          │  offers only the agent's own tools
                                          ├─ loop (bounded by maxIterations)
                                          │     └─ executor.execute(...)  ← same gates
                                          └─ returns a report
JARVIS ◀────────── structured report ─────┘
```

A sub-agent's tools are gated independently. A read-only Scout stays read-only
no matter who delegated to it, or what the delegation text says.

When the step budget runs out, the runner asks for a closing summary **with no
tools available**, so the agent reports what it actually established instead of
looping.

## Events

Every meaningful action emits a structured event on the `EventBus`. The activity
feed, the SSE stream and the persisted `events` table all read from this one
source.

| Event | Emitted when |
|---|---|
| `USER_MESSAGE` | A user message is accepted |
| `MODEL_RESPONSE` | The model produces a final answer |
| `TOOL_REQUEST` | A tool call is about to be evaluated |
| `TOOL_RESULT` | A tool has run (ok or not) |
| `MEMORY_WRITE` / `MEMORY_READ` | Memory is written / recalled |
| `AGENT_DELEGATION` / `AGENT_RESULT` | A sub-agent starts / finishes |
| `APPROVAL_REQUEST` / `APPROVAL_RESOLVED` | Human gate opened / decided |
| `ACTION_EXECUTED` | A non-`READ` action completed |
| `ERROR` | Anything failed |

Listener failures are swallowed by design — a broken UI subscriber must never
take down the orchestrator.

## Providers

```ts
interface ModelProvider {
  readonly id: string;
  readonly model: string;
  isAvailable(): boolean;                              // config complete?
  status(): ProviderStatus;                            // and if not, why not
  chat(request: ChatRequest): Promise<ChatResponse>;
  healthCheck(): Promise<{ ok: boolean; detail: string }>;
}
```

- **NVIDIA** — OpenAI-compatible; covers hosted `integrate.api.nvidia.com` and
  self-hosted NIM containers, including a local GPU. Endpoints on localhost do
  not require a key.
- **OpenAI** — OpenAI and any gateway that mirrors it.
- **Local** — the same client with no default endpoint. Not a stub: set
  `LOCAL_BASE_URL` and it works.
- **Anthropic** — Messages API. The adapter absorbs the three differences that
  matter (top-level system, `tool_use` blocks, `tool_result` inside a user
  message) so the orchestrator never learns about them.

The same pattern covers voice, image, image editing, video, vision and search.
Every one of them answers `isAvailable()` honestly and, when unconfigured,
throws `ProviderUnavailableError` with the reason — which the API surfaces as
HTTP 503 and the UI shows as "not configured". Nothing anywhere fabricates a
result to fill a gap.

## Data model

Ten tables, one migration, `user_version` tracking.

| Table | Holds |
|---|---|
| `users` | Local user (single-user in v0.1) |
| `conversations` | Conversation metadata |
| `messages` | Full transcript incl. tool calls and results |
| `memories` | Long-term memory, typed, scored, optionally expiring |
| `tasks` | Task list |
| `agents` | Registered agents and run statistics |
| `tool_calls` | Per-conversation tool history |
| `approvals` | Human approval requests and decisions |
| `audit_events` | The audit log |
| `events` | Persisted activity stream |

`ORDER BY` clauses on time columns always carry a `rowid` tiebreaker: two rows
written in the same millisecond would otherwise come back in arbitrary order.

## Composition root

[`packages/core/src/jarvis.ts`](../packages/core/src/jarvis.ts) constructs
everything once and passes it down explicitly. No globals, no service locator,
no module-level singletons — which is why a test can stand up a complete JARVIS
against an in-memory database and a scripted provider in three lines.

## Why no framework

The brief asked for orchestration a human developer can understand, and that
ruled out the large agent frameworks. What JARVIS actually needs is a message
loop, a tool registry, a permission check and a persistence layer. Those are a
few hundred lines each, they are exactly the parts that need auditing, and
every one of them is testable in isolation.

Runtime dependencies: `better-sqlite3` on the server, `react` in the browser.
Everything else — routing, validation, SSE, `.env` parsing, event bus — is
written out where it can be read.
