# Testing

```bash
npm test              # full suite
npm run test:watch    # watch mode
npm test -- tests/approvals.test.ts
npm run verify        # typecheck → test → build
```

Vitest, `node` environment, forked pool. Suites are independent: each builds its
own JARVIS against an in-memory SQLite database and a temp workspace.

## What is mocked

Only the model, and only because it is the one boundary we do not control.
`ScriptedProvider` implements `ModelProvider` and returns a scripted sequence of
turns while recording every request, so tests can assert on what the
orchestrator actually sent — the system prompt, the tool specs, the message
history after a tool result.

Everything else is real: real SQLite with real migrations, the real registry,
the real executor, the real permission policy, real files in a temp directory,
and real HTTP servers on ephemeral ports.

The NVIDIA provider is tested against a real HTTP server on localhost speaking
the OpenAI-compatible wire format, so the request body, auth header, tool
serialisation and tool-call parsing are genuinely exercised.

## Suites

| File | Covers |
|---|---|
| `config.test.ts` | `.env` parsing, defaults, `publicConfig` secret containment, redaction, repo hygiene |
| `providers.test.ts` | Provider routing, NVIDIA over real HTTP, tool serialisation, error handling, Anthropic conversion, local provider |
| `memory.test.ts` | Schema, migrations, conversations, message persistence, memory write/search/dedupe/expiry, tasks, restart persistence |
| `security.test.ts` | Approval policy, agent capability ceilings, path sandbox, JSON Schema validation |
| `tools.test.ts` | Registry, per-agent filtering, every built-in tool, sandbox refusals, unconfigured search |
| `orchestrator.test.ts` | Turn loop, tool feedback, memory injection, provider failure, iteration limit, orphaned tool messages |
| `approvals.test.ts` | Full approval workflow, denial, replay, expiry, ownership, audit of every path, redaction |
| `agents.test.ts` | Agent roster, capability boundaries, charters, delegation, sub-agent refusals, budgets |
| `api.test.ts` | Real HTTP: auth both modes, chat, conversations, memory, tools, agents, approvals, audit, SSE, media 503s, no key leakage |

## The tests that matter most

Some assert behaviour the whole design exists to guarantee:

- **`approvals.test.ts` → "holds a DESTRUCTIVE action, then executes it only
  after approval"** — the file still exists after the request and is gone after
  approval. That is the approval gate, proven on the filesystem.
- **`agents.test.ts` → "refuses a sub-agent tool call outside its authority"** —
  Scout is told to delete a file, tries, is refused, and the file survives. No
  approval is even created.
- **`api.test.ts` → "never sends an API key to the browser"** — real keys are
  set on the running config, then every UI-reachable endpoint is scanned.
- **`approvals.test.ts` → "never shows the internal approval note as a user
  message or a reply"** — a regression test for a real bug found during
  verification.
- **`tools.test.ts` → "web_search says it is unavailable instead of inventing
  results"** — the no-fabrication rule, as an assertion.

## Adding tests

Use `createTestJarvis()` from `tests/helpers.ts`:

```ts
const harness = createTestJarvis({
  turns: [
    { toolCalls: [{ name: 'current_time', arguments: {} }] },
    { content: 'It is midday.' },
  ],
});

const turn = await harness.jarvis.orchestrator.handleMessage({
  userId: harness.userId,
  text: 'what time is it?',
});

expect(turn.toolCalls).toEqual([{ name: 'current_time', status: 'executed' }]);
harness.cleanup();
```

`harness.provider.script([...])` re-scripts mid-test; `failNext()` forces a
model error; `setAvailable(false)` simulates missing configuration.

Always call `cleanup()` — it closes the database and removes the temp
workspace.

## Manual verification

Beyond the automated suite, v0.1 was verified by running the production build
and driving it over real HTTP and a real browser:

- server start, health, static UI, asset serving
- live provider check against an OpenAI-compatible endpoint
- conversation, tool call, memory write and retrieval, delegation
- the full approval cycle: request → deny (file survives) → request → approve
  (file deleted) → replay (409)
- 22 browser checks in headless Chromium: all six views render, message send,
  live SSE feed, the approval card, deny from the UI, no key in the DOM,
  responsive layout at 390px with no horizontal overflow, no console errors
