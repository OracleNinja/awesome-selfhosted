/**
 * Agent charters loaded from `/agents/<name>/agent.json`.
 *
 * The built-in definitions in definitions.ts are the defaults. An operator can
 * override any of them — prompt, tool list, risk ceiling, iteration budget — by
 * editing the JSON charter next to the agent, without touching TypeScript.
 * That is the extension point v0.2 uses to add new agents.
 *
 * Charters are operator-authored files on disk, at the same trust level as
 * `.env`. They are *not* model-writable: the file tools are confined to the
 * workspace directory, which is not the agents directory.
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import type { AgentDefinition, AgentName, RiskLevel } from '@jarvis/shared';
import { RISK_LEVELS } from '@jarvis/shared';
import { AGENT_DEFINITIONS } from './definitions.ts';

export interface AgentCharter {
  title?: string;
  purpose?: string;
  systemPrompt?: string;
  allowedTools?: string[];
  maxRisk?: RiskLevel;
  readOnly?: boolean;
  maxIterations?: number;
}

export interface CharterLoadResult {
  definitions: Record<string, AgentDefinition>;
  overridden: string[];
  errors: string[];
}

function isRiskLevel(value: unknown): value is RiskLevel {
  return typeof value === 'string' && (RISK_LEVELS as readonly string[]).includes(value);
}

function applyCharter(base: AgentDefinition, charter: AgentCharter): AgentDefinition {
  const merged: AgentDefinition = { ...base };
  if (typeof charter.title === 'string') merged.title = charter.title;
  if (typeof charter.purpose === 'string') merged.purpose = charter.purpose;
  if (typeof charter.systemPrompt === 'string' && charter.systemPrompt.trim()) {
    merged.systemPrompt = charter.systemPrompt;
  }
  if (Array.isArray(charter.allowedTools)) {
    merged.allowedTools = charter.allowedTools.map(String) as string[];
  }
  if (isRiskLevel(charter.maxRisk)) merged.maxRisk = charter.maxRisk;
  if (typeof charter.readOnly === 'boolean') merged.readOnly = charter.readOnly;
  if (typeof charter.maxIterations === 'number' && charter.maxIterations > 0) {
    merged.maxIterations = Math.min(20, Math.floor(charter.maxIterations));
  }
  return merged;
}

/**
 * Load agent definitions, applying any charter overrides found in `agentsDir`.
 * A malformed charter is reported and ignored — never fatal at startup.
 */
export function loadAgentDefinitions(agentsDir: string): CharterLoadResult {
  const definitions: Record<string, AgentDefinition> = {};
  for (const [name, definition] of Object.entries(AGENT_DEFINITIONS)) {
    definitions[name] = { ...definition };
  }

  const overridden: string[] = [];
  const errors: string[] = [];

  if (!existsSync(agentsDir)) return { definitions, overridden, errors };

  let entries: string[];
  try {
    entries = readdirSync(agentsDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
  } catch (error) {
    errors.push(`could not read ${agentsDir}: ${(error as Error).message}`);
    return { definitions, overridden, errors };
  }

  for (const name of entries) {
    const charterPath = join(agentsDir, name, 'agent.json');
    if (!existsSync(charterPath)) continue;

    const base = definitions[name];
    if (!base) {
      errors.push(`${name}/agent.json: no built-in agent named "${name}" (adding new agents lands in v0.2)`);
      continue;
    }

    try {
      const charter = JSON.parse(readFileSync(charterPath, 'utf8')) as AgentCharter;
      const merged = applyCharter(base, charter);
      const changed = JSON.stringify(merged) !== JSON.stringify(base);
      definitions[name] = merged;
      if (changed) overridden.push(name);
    } catch (error) {
      errors.push(`${name}/agent.json: ${(error as Error).message}`);
    }
  }

  return { definitions, overridden, errors };
}

export function isAgentName(value: string): value is Exclude<AgentName, 'jarvis'> {
  return value in AGENT_DEFINITIONS;
}
