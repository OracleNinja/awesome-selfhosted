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
