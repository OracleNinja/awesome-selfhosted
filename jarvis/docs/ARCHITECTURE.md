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

## Turns

A *turn* is one unit of user work: a message, everything it causes, and the
reply. It is the unit of cancellation and the unit of correlation.

```
turn_9fd2…  ── model call ──▶
            ├─ tool call A ─▶  same turnId, same signal
            ├─ tool call B ─▶
            ├─ delegated agent ─┬─ its model calls
            │                   └─ its tool calls
            └─ model call ──▶
```

`TurnRegistry.begin()` mints the id and the controller; `handleMessage` releases
it in a `finally`, so completion, failure and cancellation all clean up the same
way and the registry's size is a live count rather than a leak. A delegated
sub-agent shares its caller's turn — it is the same unit of user work, so
cancelling stops it too and its tool calls correlate to the same id.

The id reaches `events`, `audit_events` and `tool_calls`, which is what makes
"show me everything that happened during this turn" a single query per table
rather than three timestamp scans. Columns are nullable: rows written before
v0.2 have no turn, and reading a null is a better trade than destroying history.

### Parent turns

A turn started by resolving an approval records `parentTurnId` — the turn that
originally asked. This closes the one correlation gap turn ids alone could not:
an approved action runs minutes later, in a new turn, and without the link the
audit trail shows an execution with no visible cause.

The link survives a restart because it is recovered from the approval record
(`AuditRepo.turnForApproval`) rather than held in memory. It is written on
`events` and `audit_events` and is nullable, so an ordinary turn simply has no
parent.

`parentTurnId` is **correlation metadata and nothing else**. It grants no
authority, is never read by the permission or approval gates, and a turn that
claimed a parent it did not have would gain exactly nothing by it.

## Capabilities

A *capability* is anything the model can invoke. Local tools and remote MCP
tools are the same kind of thing to every layer above the registry — the point
of the design is that there is no second inventory to consult and no second
code path to secure.

[`ToolRegistry`](../packages/tools/src/registry.ts) holds a `CapabilityRecord`
per capability: the definition, its provenance (`{kind:'local'}` or
`{kind:'mcp', server, remoteName}`), whether it is enabled, and why not when it
is not. Disabling is deliberately distinct from removing — a disconnected MCP
server's capabilities stay visible with a reason attached, so a model asking for
one gets "unavailable because …" rather than "no such tool".

Remote capabilities are namespaced `mcp__<server>__<tool>`, which is enforced at
registration in both directions: an MCP capability must carry the prefix, and a
local one may not. A remote server therefore cannot shadow, replace or collide
with a local capability no matter what it names its tools.

### MCP

[`packages/mcp`](../packages/mcp) speaks JSON-RPC 2.0 over newline-delimited
JSON on stdio, written directly rather than pulled in as a dependency — the
protocol surface JARVIS uses is small, and a client is where the trust boundary
lives, so it is worth owning.

```
McpManager ── per server ──▶ McpClient (child process, stdio)
     │                            │
     │                            └─ listTools / callTool, bounded and shape-checked
     └─ adapter ──▶ ToolDefinition ──▶ ToolRegistry ──▶ the same executor as a local tool
```

Three properties matter:

- **Failure is per-server.** A server that will not start, hangs during the
  handshake, or returns nonsense from `tools/list` disables its own capabilities
  and nothing else. `connectAll()` never throws.
- **Remote metadata is data.** Names, descriptions, schemas and annotations are
  bounded, sanitised and — crucially — never consulted for authority. See
  [SECURITY](SECURITY.md#mcp-and-remote-capabilities).
- **JARVIS owns the clock.** The per-call timeout is the runtime's, injected
  into the client; a remote server cannot extend it. Cancellation sends
  `notifications/cancelled` as a courtesy and stops waiting regardless.

Servers are declared in configuration (`MCP_SERVERS`). There is no endpoint,
tool or model-reachable path that connects to a new server: configuration
establishes availability, and nothing at runtime can add to it.

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

### Capability-based routing

The orchestrator asks for *capabilities*, not vendors: offering tools on a turn
means the call requires `toolUse`, so that is what it requests.
[`ModelRouter`](../packages/providers/src/routing.ts) answers with the first
provider in a declared order that is both available and capable — configured
default first, then `MODEL_ROUTING_FALLBACK`.

This is deliberately **not** difficulty routing. Deciding "this question is
hard" before answering it costs either a second model call or a heuristic that
is wrong in both directions, and a wrong route is worse than no route.
Capabilities are facts about a model; difficulty is a guess about a request.

Capabilities are declared per provider (`<PROVIDER>_CAPABILITIES`) because they
depend on which model is configured, and JARVIS cannot interrogate a remote model
to find out. An undeclared capability is treated as absent. When nothing can
serve a request the router throws `NoCapableModelError` listing what it
considered — a clear failure rather than a quiet downgrade to a model that will
answer confidently and wrongly.

The same pattern covers voice, image, image editing, video, vision and search.
Every one of them answers `isAvailable()` honestly and, when unconfigured,
throws `ProviderUnavailableError` with the reason — which the API surfaces as
HTTP 503 and the UI shows as "not configured". Nothing anywhere fabricates a
result to fill a gap.

## Data model

Eleven tables, three append-only migrations, `user_version` tracking.

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
| `model_usage` | Per-call tokens, latency and routing decision |

`ORDER BY` clauses on time columns always carry a `rowid` tiebreaker: two rows
written in the same millisecond would otherwise come back in arbitrary order.

Migrations only add. `turn_correlation` (v2) added nullable `turn_id` columns;
`usage_and_parent_turn` (v3) added `model_usage` and nullable `parent_turn_id`.
Nothing rewrites or drops existing rows, so an older database keeps its history
and simply reads null for what it never recorded.

`model_usage` stores tokens, latency, outcome and the routing decision that
produced the call. Token columns are nullable and the total is derived only when
both halves are known — a zero would read as "free", which is a different claim
from "not reported". There is no cost column: pricing changes outside the
runtime and hard-coding it would produce confident, stale numbers.

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
