# Roadmap

v0.1 is the foundation: orchestration, memory, tools, permissions, approvals,
audit, agents, providers and the mission-control UI. Everything below is
deliberately *not* built yet, with a note on where it plugs in.

## The extension seams

Almost everything on this list attaches at one of four places:

1. **`ToolDefinition`** — anything the model can invoke.
2. **`ModelProvider`** and the media/voice/search provider interfaces.
3. **`AgentDefinition`** — new specialists, or new charters for existing ones.
4. **The event bus** — anything that reacts to what JARVIS did.

If a feature does not fit one of those, that is a signal the architecture needs
a change rather than another integration.

## v0.2

### MCP (first priority)

An MCP client that maps each remote tool to a `ToolDefinition` gets permissions,
approvals and audit for free. Needs: a risk-level mapping policy (an unknown
remote tool should default to `EXTERNAL_ACTION`, not `READ`), per-server
allow-lists, and connection lifecycle in the composition root.

### Business and productivity integrations

Gmail, Google Calendar, Google Drive, GitHub, Stripe, 7shifts, POS systems.
Each is a tool module with OAuth credentials in `.env`. Classification is the
important part: reads are `READ`, sends and writes are `EXTERNAL_ACTION` — which
means they stop for approval by default, which is correct for anything that
touches money or a customer.

### Scheduled routines

Cron-style triggers that run a Scout or Advisor task on a schedule and file the
result as a memory, a task or a notification. Needs a scheduler in the server, a
`routines` table, and a rule that a routine runs with the same permission gates
as an interactive turn — an unattended agent must not be able to approve itself.

### Notifications

Push, email or webhook when an approval is pending or a routine finds something.
Subscribes to the event bus; no orchestrator changes.

### RAG, embeddings, semantic memory

`MemoryRepo.search()` already has the shape an embedding-backed implementation
needs. Swap the lexical scorer for vector similarity (sqlite-vec, or an external
store), keep the interface, and add document ingestion as a separate table.
Hybrid lexical + vector will beat either alone.

### Richer voice

Streaming STT for barge-in, wake-word activation, voice selection in Settings,
and playback controls. The provider interfaces already support server-side
audio; what is missing is streaming rather than request/response.

### Image and video in the conversation

The providers exist and work. What v0.2 adds is tools (`image_generate`,
`image_edit`, `video_generate`) so the model can invoke them, plus attachment
storage and rendering in the transcript. Generation is `EXTERNAL_ACTION` — it
spends money.

### Browser automation and computer use

The highest-risk item on this list. Requires its own risk level or a mandatory
approval on every action, a strict domain allow-list, and a visible action log.
Not to be added casually.

## Beyond v0.2

- **Multi-user** — real sessions, per-user memory isolation, roles. `userId` is
  already threaded through every table and API call; what is missing is
  authentication that establishes identity rather than access.
- **Local NVIDIA GPU inference** — already reachable today by pointing
  `NVIDIA_BASE_URL` or `LOCAL_BASE_URL` at a local NIM or vLLM. A first-class
  version would add model management and GPU health to the UI.
- **Per-agent model routing** — Scout on a cheap fast model, Developer on a
  strong one. `AgentRunner` already takes a provider; it needs to become a
  lookup rather than a constant.
- **Cost tracking** — token usage is already returned by providers and carried
  in `MODEL_RESPONSE` events; it is not yet aggregated or displayed.
- **Streaming responses** — the wire format supports it; the orchestrator loop
  and the UI would both need to handle partial output.
- **Encrypted memory at rest**, **audit log signing**, **approval policies per
  tool rather than per risk level**.

## Explicitly not planned

- A plugin system with dynamic code loading. Tools are TypeScript in the repo;
  MCP covers the remote case with a real security boundary.
- An unrestricted shell tool. See [SECURITY.md](SECURITY.md).
- Swapping the orchestrator for a framework. The loop being readable is a
  feature.
