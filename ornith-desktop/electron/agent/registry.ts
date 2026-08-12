import type { PermissionBroker, PermissionDecision, ToolDefinition, ToolRegistry } from './types';
import { log } from '../log';

/**
 * V1: deliberately empty. No tool is registered, so the model cannot request
 * one. Do not populate this without also implementing the permission flow in
 * SPEC §11.5 and §15.1.
 */
export function createToolRegistry(): ToolRegistry {
  const tools = new Map<string, ToolDefinition>();

  return {
    list: () => [...tools.values()],
    get: (name) => tools.get(name),
    register(tool) {
      if (tools.has(tool.name)) throw new Error(`Tool already registered: ${tool.name}`);
      tools.set(tool.name, tool);
    },
  };
}

/**
 * V1: denies everything unconditionally. A model request is a proposal, never
 * an authorisation — that invariant holds from the first release so it cannot
 * be quietly relaxed later.
 */
export function createDenyAllBroker(): PermissionBroker {
  return {
    async request(req): Promise<PermissionDecision> {
      log.warn('permission.denied', { tool: req.call.tool, risk: req.risk, reason: 'v1-deny-all' });
      return { deny: true };
    },
  };
}
