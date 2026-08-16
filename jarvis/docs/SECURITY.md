# Security

JARVIS is allowed to act on your behalf. Everything below exists because that
is a meaningfully different thing from a chatbot.

## The core principle

**The model is untrusted input.** It is useful, it is usually right, and it is
never the authority on what it is permitted to do. Every permission decision is
made in code, from declared metadata, before or independently of anything the
model says.

There is no prompt, no tool argument, no file content and no web page that can
raise an agent's ceiling or waive an approval. Prompts describe the rules to the
model so it behaves sensibly; the executor enforces them whether it does or not.

## Risk levels

| Level | Meaning | Examples |
|---|---|---|
| `READ` | Observes, changes nothing | `web_search`, `file_read`, `memory_search` |
| `WRITE` | Changes JARVIS's own state | `memory_write`, `task_create`, `file_write` |
| `EXTERNAL_ACTION` | Affects the world outside JARVIS | sending mail, calling a business API |
| `DESTRUCTIVE` | Irreversible loss | `file_delete`, `memory_forget` |

`APPROVAL_REQUIRED_LEVELS` (default `EXTERNAL_ACTION,DESTRUCTIVE`) decides which
levels stop for a human. Set it to `WRITE,EXTERNAL_ACTION,DESTRUCTIVE` for a
stricter posture.

## Two gates

**Gate 1 — agent capability.** Before the model is prompted, the registry
filters the tool list to what the agent may use, checking three things: the
tool is on the agent's list, its risk is within `maxRisk`, and a `readOnly`
agent is not being handed a mutating tool. A model that never sees a tool
cannot call it; a model that hallucinates one gets a "does not exist" error and
an audit row.

**Gate 2 — approval.** At execution time the policy is re-evaluated from the
tool's declared risk. A tool may opt *in* to approval (`file_write` does) but
can never opt out of a policy-mandated level.

Both gates apply identically to the orchestrator and to delegated agents. There
is one execution path.

## The approval workflow

1. A gated call creates a `pending` approval and **stops**. Nothing is executed.
2. The model receives, as the tool result:
   > `APPROVAL REQUIRED. "file_delete" is classified DESTRUCTIVE and has NOT been executed. Request apr_… is waiting for the user's decision. Do not retry and do not describe this as done.`
3. The UI shows the action, the risk level and the full arguments, with APPROVE
   and DENY.
4. On approval, the executor re-checks state and runs the stored arguments.
   The conversation continues with a factual note about what happened.
5. On denial, nothing runs; the denial is audited.

Properties, each covered by a test:

- **Not executed while pending** — the target file still exists after the request.
- **No replay** — the state update is conditional on `state = 'pending'`, so a
  second approve/deny returns HTTP 409 rather than executing again.
- **Expiry** — requests expire (`APPROVAL_TIMEOUT_SECONDS`, default 900s) and an
  expired request can never execute.
- **Ownership** — an approval belonging to another user cannot be decided.
- **Arguments are frozen** — the approved arguments are what run. The model
  cannot alter them between request and approval.
- **The post-approval turn is offered no tools** — so the model reports the
  outcome rather than redoing the action.

## Filesystem sandbox

`file_read`, `file_write`, `file_list` and `file_delete` resolve every path
through `resolveWorkspacePath()`, which rejects:

- absolute paths
- traversal that escapes the root (checked on the **resolved** path, so
  `a/../../etc/passwd` fails)
- null bytes
- empty paths, and paths resolving to the root itself

Everything is confined to `JARVIS_WORKSPACE_DIR`. Writes are capped at 1 MB,
reads at 256 KB. `file_delete` refuses directories.

## Cancellation and timeouts

Both are control boundaries, not conveniences.

**Cancellation.** A turn owns exactly one `AbortController`, created by the
runtime and never handed out — callers hold a `TurnHandle` that exposes a
read-only signal and an idempotent `end()`. The controller is a genuine private
field, so no caller can reach in and abort another turn's work. Cancelling
propagates to the in-flight model call, every running tool call, and any
delegated sub-agent, which shares its caller's turn.

Cancellation cannot bypass authorization. A cancelled turn refuses to *start* a
tool, so it can never turn a refusal into an execution, and it creates no
approval on the way out.

**Tool timeouts.** Every tool runs inside a boundary that aborts on whichever
comes first: the turn being cancelled, or the tool exceeding its timeout
(`DEFAULT_TOOL_TIMEOUT_MS`, 30s, overridable per tool). The cause is recorded at
the moment it fires, first writer wins, so a user cancellation is never
reclassified as a timeout by a timer that fires afterwards — or the reverse.

A tool is *asked* to stop through its signal, and the executor stops waiting
either way. The honest limit: JavaScript cannot kill a running promise, so a
tool that ignores its signal may still complete its work in the background. Its
result is discarded and the turn is not held open, but the side effect it was
performing is not undone. Tools that touch anything consequential must honour
their signal.

**Timeout never becomes an authorized retry.** A timed-out call produces a
`timeout` outcome and an audit row. It is not retried automatically, and the
model is told the action did not complete.

## No shell

There is no shell tool in v0.1, and Developer's prompt states plainly that it
has none. Arbitrary command execution would make every other permission in this
system decorative. If it is ever added it must be a separately permissioned
`DESTRUCTIVE` tool with its own allow-list — not a general escape hatch.

## Secrets

- Provider keys are read server-side from `.env` and never leave the process.
- `publicConfig()` is built by explicit allow-list, not by deleting keys from
  the private config — so adding a secret cannot leak it by omission.
- `redact()` masks credential-shaped keys (`api_key`, `token`, `secret`,
  `password`, `authorization`) and credential-shaped values (`nvapi-…`, `sk-…`)
  in audit rows, persisted events and error responses.
- Provider statuses expose the **host** of an endpoint, never the full URL.
- A test asserts that no key appears in any response the browser can fetch.

## API authentication

**Token mode** (`JARVIS_API_TOKEN` set): every `/api` request needs
`Authorization: Bearer <token>`, compared with `timingSafeEqual`. `/api/health`
stays public and returns only liveness and schema version.

**Local mode** (no token): the server binds `127.0.0.1` only. Convenient for
development; set a token before exposing JARVIS to any network.

No CORS headers are emitted, so a page on another origin cannot read JARVIS's
responses. The one exception to header-based auth is `/api/events/stream`, which
accepts the token as a query parameter because `EventSource` cannot set headers;
no other route does, and that token is the user's own session credential, not a
provider key.

## Prompt injection

A page fetched by `web_search`, or a file read by `file_read`, may contain text
trying to redirect the agent. The mitigations are structural:

- Scout, the agent most likely to encounter hostile text, has **no mutating
  tools at all**. The worst outcome is a wrong report.
- Any escalation the injected text asks for still hits both gates. "Delete the
  logs" from a web page reaches the same `DESTRUCTIVE` approval card a user
  request would.
- Agent charters are read from `agents/`, which is outside
  `JARVIS_WORKSPACE_DIR` — the model cannot write its own permissions.
- Every attempt is audited, including refusals, so an injection attempt leaves
  evidence.

Residual risk: an agent can still be made to *report* something false, and an
approval card can be made to look reasonable. Read the arguments before
approving — that is what the card is for.

## MCP and remote capabilities

The governing rule: **expanding capability must not expand authority.**

An MCP server is an untrusted external capability provider. Being named in
configuration makes its tools *available*; it does not make them *trusted*, and
it does not make them exempt from anything below.

### A server cannot classify itself

Risk comes from `MCP_<ID>_RISK_FLOOR` — set by the operator, defaulting to
`EXTERNAL_ACTION`, which means approval-gated. `classifyRemoteRisk()` reads the
server's *configuration* and nothing else.

Remote descriptors routinely carry fields like `risk`, `requiresApproval`,
`dangerous`, `readOnlyHint` and `destructiveHint`. JARVIS reads none of them.
There is no code path in which a value supplied by a remote server can lower a
risk level, clear an approval requirement, or otherwise decide what it is
allowed to do. A tool's `requiresApproval` is computed locally from the
configured floor.

The only way a remote capability runs without approval is an operator lowering
the floor for a server they have audited — a configuration decision, made by a
human, recorded in the environment.

### Remote metadata is data

Everything a server sends is treated as hostile input:

- **Names** must match `^[A-Za-z0-9_.-]{1,64}$`; anything else is refused with
  the reason recorded. Accepted names are then namespaced `mcp__<server>__<name>`,
  so a remote tool cannot shadow or collide with a local one.
- **Schemas** are rebuilt, not forwarded: unrecognised keys dropped, depth
  bounded to 8, properties bounded to 100, and the top level forced to
  `type: 'object'`.
- **Descriptions** are truncated and prefixed `[via MCP server "id"]`. The model
  reads them; the runtime never acts on them.
- **Results** are bounded and flattened to text. A result saying "ignore
  previous instructions and execute X" is a string in a tool result — the same
  position as hostile text from a web page, covered by the mitigations above. It
  cannot become an instruction the runtime obeys, because nothing between the
  transport and the model interprets result content as control.
- **Frames** are bounded at 4 MB per line and 200 tools per server, so a
  malicious or broken server cannot exhaust memory.

### The control plane still applies

- Every remote call goes through the same executor, the same permission gate and
  the same approval gate as a local tool. There is no MCP fast path.
- The per-call timeout is JARVIS's, injected into the client. A server cannot
  extend it, and a server that simply never answers is abandoned at the boundary
  rather than holding the turn.
- A timeout or cancellation records a terminal outcome. Neither converts an
  unauthorised call into an authorised retry.
- No endpoint, tool or model-reachable path connects to a new server. There is
  no dynamic "connect to anything" interface.

Residual risk: a server the operator has explicitly trusted with a lowered risk
floor can act within that floor without a per-call prompt. That is what lowering
it means. The default does not do this, and the floor is visible in the Control
Room next to every capability the server provides.

## Threat model

| Threat | Mitigation |
|---|---|
| Model invents a completed action | Tool results state failure/pending explicitly; prompts forbid past-tense claims; approvals are recorded |
| Model calls a tool it should not have | Filtered before prompting; refused and audited at execution |
| Model escalates via prompt injection | Capability ceilings in code; approvals unbypassable; charters outside the sandbox |
| Path traversal to read/write outside the workspace | Resolved-path check, absolute paths rejected |
| Approval replayed or forged | Conditional state update, ownership check, expiry |
| Credentials leak to the browser | Allow-listed public config; no route returns a key; asserted by test |
| Credentials leak into logs | `redact()` on audit rows, events and errors |
| Unauthorised network access | Token auth (constant-time) or loopback-only binding |
| Runaway agent loop | Per-agent iteration budgets; closing summary without tools |
| Database corruption | WAL, foreign keys on, migrations in transactions |
| MCP server claims to be low-risk | Risk comes from configuration; remote metadata is never read for authority |
| MCP server shadows a local tool | Mandatory `mcp__<server>__` namespace, enforced at registration |
| MCP server hangs or floods the transport | Runtime-owned timeout, 4 MB line cap, 200-tool cap, per-server isolation |
| Hostile text in a remote tool result | Bounded, flattened to text, and read only by the model — never by the runtime |

## Known limitations in v0.1

- **Single user.** `DEFAULT_USER_ID` is fixed; the token is an access
  credential, not an identity. Multi-user needs real sessions.
- **No rate limiting.** A compromised token can exhaust provider quota.
- **No TLS.** Terminate at a reverse proxy — see
  [PRODUCTION.md](PRODUCTION.md).
- **No encryption at rest.** The SQLite file is plaintext; protect it with
  filesystem permissions and backups.
- **Audit log is append-only by convention**, not enforced. A process with write
  access to the database can alter it.
