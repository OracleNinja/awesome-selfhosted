# Ornith Cloud

The online backend for [Ornith Desktop](../ornith-desktop). It gives the desktop app a second brain beside its local Ollama one: a fast hosted model, plus bounded web retrieval when a question actually needs current information.

The desktop app talks only to this gateway. **Provider API keys live here and never reach the desktop or its renderer.**

```
Ornith Desktop ──HTTPS/SSE──▶ Ornith Cloud ──▶ Search provider
                                           └─▶ AI provider
```

## What it does

1. Authenticates the request (bearer token).
2. Classifies the question — deterministic string analysis, no model call, so it costs nothing.
3. Retrieves only when the question needs current information: search → rank → fetch 3 pages **concurrently** → extract readable text → bound the total.
4. Assembles a prompt that fences retrieved content as untrusted data.
5. Streams the answer back as Server-Sent Events, with sources and latency timings.

## Environment

Copy `.env.example` to `.env`. Never commit `.env`.

| Variable | Required | Purpose |
|---|---|---|
| `ORNITH_GATEWAY_TOKEN` | **yes** | Bearer token the desktop must present. The server refuses to start without it |
| `AI_PROVIDER` | no | `anthropic` (default) or `stub` |
| `ANTHROPIC_API_KEY` | yes for `anthropic` | Provider credential |
| `ORNITH_ONLINE_MODEL` | no | Model for normal questions |
| `ORNITH_FAST_MODEL` | no | Cheaper model for arithmetic, conversions, greetings |
| `SEARCH_API_KEY` | no | Brave Search API key. **Retrieval is disabled without it** |
| `SEARXNG_URL` | no | Self-hosted alternative, used only when `SEARCH_API_KEY` is unset |
| `PORT` | no | Default `8787` |

Generate a token:

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"
```

Retrieval limits (`ORNITH_SEARCH_RESULTS`, `ORNITH_DOCUMENTS_FETCHED`, `ORNITH_MAX_DOCUMENT_CHARS`, `ORNITH_MAX_CONTEXT_CHARS`) and timeouts are all configurable — see `.env.example`.

## Run

```bash
npm install
npm run typecheck
npm test
npm run dev
```

Health check:

```bash
curl http://localhost:8787/health
# {"status":"ok","service":"ornith-cloud","version":"0.1.0"}
```

Chat (streams SSE):

```bash
curl -N http://localhost:8787/v1/chat \
  -H "Authorization: Bearer $ORNITH_GATEWAY_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is the current price of XRP?"}]}'
```

## Wire protocol

`POST /v1/chat` returns `text/event-stream`:

```
event: status    data: {"type":"status","status":"searching"}
event: sources   data: {"type":"sources","sources":[{"title":"…","url":"…","domain":"…","cached":false}]}
event: status    data: {"type":"status","status":"generating"}
event: delta     data: {"type":"delta","content":"Hello"}
event: complete  data: {"type":"complete","timings":{…},"usage":{…}}
```

Errors arrive as `event: error` with a `code` and a safe `message`; provider details are logged server-side only.

**SSE writes are serialised through a queue.** Provider callbacks are synchronous, so an un-awaited write can still be in flight when the stream closes — which silently loses the event. A real bug found by testing, not a hypothetical.

## Connecting Ornith Desktop

In the desktop app: **Settings → Connection mode → Online**, set the gateway URL (e.g. `http://localhost:8787`) and paste the token.

The token field is **write-only**: `settings:get` returns `gatewayTokenConfigured: boolean`, never the value, so the secret never enters renderer memory. The HTTPS call is made by the Electron main process — the renderer runs with `connect-src 'none'` and cannot reach the network at all.

## Local vs Online

| | Local | Online |
|---|---|---|
| Inference | Ollama on your Mac | Hosted provider via this gateway |
| Web retrieval | No | Yes, when the question needs it |
| Conversation data | Stays on the Mac | Sent to the gateway and provider |
| Works offline | Yes | No |

The desktop asks once, on first run, and never silently switches. The active mode is always visible in the sidebar. Voice stays local in both modes: audio is transcribed and spoken on-device, and only text crosses the network.

## Provider adapters

```
src/ai/types.ts              AIProvider interface
src/ai/anthropic.ts          hosted AI adapter
src/ai/stub.ts               deterministic provider for tests
src/retrieval/types.ts       SearchProvider / FetchProvider interfaces
src/retrieval/hosted-search.ts   Brave Search adapter
src/retrieval/search.ts      SearXNG adapter + null provider
```

Nothing above these files names a vendor. Adding Tavily, OpenAI, or a local model means adding one file and one config branch.

## Prompt injection

Retrieved pages are attacker-controlled. They are fenced in `<retrieved_context>`, tags inside them are neutralised so a page cannot close the fence, and the system prompt states that the block is data and must never be followed as instructions. This is tested with a page containing `IGNORE ALL PREVIOUS INSTRUCTIONS` — see `tests/prompt.test.ts`.

## MCP

**Intentionally not implemented.** The local `ornith-en` model reports `capabilities: ['completion']` and Ollama rejects tool calls outright, so there is nothing to drive an agent loop yet. The provider interfaces are shaped so a `ToolProvider` can be added later without redesigning the gateway.
