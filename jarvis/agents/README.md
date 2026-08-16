# Agents

Each subdirectory is an agent's charter. The executable defaults live in
[`packages/agents/src/definitions.ts`](../packages/agents/src/definitions.ts);
the files here document each agent and provide the override mechanism.

## Overriding an agent

Drop an `agent.json` next to an agent's README and JARVIS merges it over the
built-in definition at startup. Any subset of these keys is accepted:

```json
{
  "title": "Recon",
  "purpose": "One-line description shown in the Agents view.",
  "systemPrompt": "Replaces the built-in prompt entirely.",
  "allowedTools": ["web_search", "memory_search", "current_time"],
  "maxRisk": "READ",
  "readOnly": true,
  "maxIterations": 6
}
```

A malformed charter is reported on startup (and in the Settings view) and then
ignored — a bad JSON file never stops JARVIS from booting.

Charters are operator-authored files, at the same trust level as `.env`. They
are **not** model-writable: the `file_write` tool is confined to
`JARVIS_WORKSPACE_DIR`, which is a different directory.

Each directory also carries an `agent.json.example`. Rename it to `agent.json`
to activate it.

## The permission model still applies

A charter cannot grant an agent more than the system allows. Whatever
`maxRisk` says, an `EXTERNAL_ACTION` or `DESTRUCTIVE` tool call still stops for
human approval, and every call is still written to the audit log.
