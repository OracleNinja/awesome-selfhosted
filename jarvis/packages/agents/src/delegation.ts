/**
 * Delegation, exposed to the orchestrator's model as a tool.
 *
 * Making delegation a tool (rather than a hidden router) means the decision to
 * delegate shows up in the same event stream, audit log and approval path as
 * everything else. The sub-agent's own tool calls are gated independently — a
 * READ-only agent stays read-only no matter who delegated to it.
 */
import type { AgentDefinition, ToolDefinition } from '@jarvis/shared';
import { truncate } from '@jarvis/shared';
import type { AgentRunContext, AgentRunner } from './runner.ts';

export interface DelegationDeps {
  runner: AgentRunner;
  definitions: Record<string, AgentDefinition>;
  /** Retrieves memory context for the sub-agent, same as the orchestrator uses. */
  contextFor?: (userId: string, task: string) => AgentRunContext['memories'];
}

export function delegateAgentTool(deps: DelegationDeps): ToolDefinition {
  const names = Object.keys(deps.definitions).sort();
  const roster = Object.values(deps.definitions)
    .map((agent) => `${agent.name} — ${agent.purpose}`)
    .join('; ');

  return {
    name: 'delegate_agent',
    description:
      `Hand a self-contained task to a specialist agent and receive its report. Agents: ${roster}. ` +
      'Give the agent everything it needs in the task text — it cannot see this conversation.',
    risk: 'WRITE',
    requiresApproval: false,
    // Delegation runs a whole sub-agent: up to maxIterations model calls, each
    // with its own provider timeout. The default tool budget would abort a
    // legitimate run, so this is the one justified override.
    timeoutMs: 300_000,
    inputSchema: {
      type: 'object',
      properties: {
        agent: { type: 'string', description: 'Which specialist to use.', enum: names },
        task: {
          type: 'string',
          description: 'The complete, self-contained task. Include all necessary context.',
          minLength: 8,
          maxLength: 8000,
        },
        briefing: {
          type: 'string',
          description: 'Optional extra background for the agent.',
          maxLength: 4000,
        },
      },
      required: ['agent', 'task'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const agentName = String(args.agent ?? '');
      const definition = deps.definitions[agentName];
      if (!definition) {
        return {
          ok: false,
          summary: `No agent named "${agentName}".`,
          error: `unknown agent "${agentName}"; available: ${names.join(', ')}`,
        };
      }

      const task = String(args.task ?? '');
      // The sub-agent runs inside the caller's turn: same unit of user work,
      // so cancelling the turn stops it and its tool calls share the turnId.
      const runContext: AgentRunContext = {
        userId: ctx.userId,
        conversationId: ctx.conversationId,
        turn: { turnId: ctx.turnId, signal: ctx.signal ?? new AbortController().signal },
      };
      const memories = deps.contextFor?.(ctx.userId, task);
      if (memories && memories.length > 0) runContext.memories = memories;
      if (typeof args.briefing === 'string' && args.briefing) runContext.briefing = args.briefing;

      const result = await deps.runner.run(definition, task, runContext);

      const ok = result.stoppedBecause !== 'error';
      const summary = ok
        ? `${definition.title} completed in ${result.iterations} step${result.iterations === 1 ? '' : 's'}` +
          (result.stoppedBecause === 'awaiting_approval' ? ' (one or more actions await approval)' : '') +
          (result.stoppedBecause === 'max_iterations' ? ' (stopped at its step limit)' : '') +
          `: ${truncate(result.output, 120)}`
        : `${definition.title} failed: ${result.error ?? 'unknown error'}`;

      return {
        ok,
        summary,
        data: {
          agent: result.agent,
          report: result.output,
          iterations: result.iterations,
          toolCalls: result.toolCalls,
          stoppedBecause: result.stoppedBecause,
        },
        ...(result.error ? { error: result.error } : {}),
      };
    },
  };
}
