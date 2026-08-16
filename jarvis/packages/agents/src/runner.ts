/**
 * The agent runner.
 *
 * Delegation is a bounded loop, not a framework: build a message list, ask the
 * model, run whatever tools it asked for through the executor, feed the results
 * back, stop when the model stops asking or the iteration budget runs out.
 *
 * The runner takes the executor as a port rather than importing it, which keeps
 * the dependency arrow pointing one way (core → agents) and lets tests drive an
 * agent with a fake executor.
 */
import type {
  AgentDefinition,
  AgentRunResult,
  ChatMessage,
  MemorySearchResult,
  ToolResult,
} from '@jarvis/shared';
import { EventBus, truncate } from '@jarvis/shared';
import type { ModelProvider } from '@jarvis/providers';
import type { ToolRegistry } from '@jarvis/tools';

export interface ExecutionOutcome {
  status: 'executed' | 'awaiting_approval' | 'denied' | 'error';
  result: ToolResult | null;
  approvalId?: string | null;
  /** Model-facing description of what happened. Always truthful. */
  message: string;
}

export interface ToolExecutorPort {
  execute(request: {
    tool: string;
    args: Record<string, unknown>;
    agent: AgentDefinition;
    userId: string;
    conversationId: string | null;
    signal?: AbortSignal;
  }): Promise<ExecutionOutcome>;
}

export interface AgentRunContext {
  userId: string;
  conversationId: string | null;
  /** Memories injected as context, retrieved by the caller. */
  memories?: MemorySearchResult[];
  /** Extra background the orchestrator wants the agent to have. */
  briefing?: string;
  signal?: AbortSignal;
}

export interface AgentRunnerDeps {
  provider: ModelProvider;
  registry: ToolRegistry;
  executor: ToolExecutorPort;
  events: EventBus;
  onAgentRun?: (agentName: string) => void;
}

export class AgentRunner {
  constructor(private readonly deps: AgentRunnerDeps) {}

  async run(
    agent: AgentDefinition,
    task: string,
    ctx: AgentRunContext,
  ): Promise<AgentRunResult> {
    const { provider, registry, executor, events } = this.deps;
    const toolCallLog: { name: string; ok: boolean }[] = [];

    this.deps.onAgentRun?.(agent.name);
    events.emit({
      type: 'AGENT_DELEGATION',
      userId: ctx.userId,
      conversationId: ctx.conversationId,
      agent: agent.name,
      summary: `${agent.title} started: ${truncate(task, 100)}`,
      data: { task, maxIterations: agent.maxIterations },
    });

    if (!provider.isAvailable()) {
      const message = provider.status().reason ?? 'the configured model provider is unavailable';
      events.emit({
        type: 'ERROR',
        userId: ctx.userId,
        conversationId: ctx.conversationId,
        agent: agent.name,
        summary: `${agent.title} could not start: ${message}`,
        data: { reason: message },
      });
      return {
        agent: agent.name,
        output: `${agent.title} could not run: ${message}`,
        iterations: 0,
        toolCalls: [],
        stoppedBecause: 'error',
        error: message,
      };
    }

    const messages: ChatMessage[] = [
      { role: 'system', content: this.buildSystemPrompt(agent, ctx) },
      { role: 'user', content: task },
    ];
    const specs = registry.specsForAgent(agent);

    let iterations = 0;
    let awaitingApproval = false;

    while (iterations < agent.maxIterations) {
      iterations += 1;

      let response;
      try {
        response = await provider.chat({
          messages,
          tools: specs,
          temperature: 0.3,
          ...(ctx.signal ? { signal: ctx.signal } : {}),
        });
      } catch (error) {
        const message = (error as Error).message;
        events.emit({
          type: 'ERROR',
          userId: ctx.userId,
          conversationId: ctx.conversationId,
          agent: agent.name,
          summary: `${agent.title} model call failed: ${truncate(message, 120)}`,
          data: { error: message },
        });
        return {
          agent: agent.name,
          output: `${agent.title} failed: ${message}`,
          iterations,
          toolCalls: toolCallLog,
          stoppedBecause: 'error',
          error: message,
        };
      }

      if (response.toolCalls.length === 0) {
        events.emit({
          type: 'AGENT_RESULT',
          userId: ctx.userId,
          conversationId: ctx.conversationId,
          agent: agent.name,
          summary: `${agent.title} finished after ${iterations} step${iterations === 1 ? '' : 's'}.`,
          data: { iterations, toolCalls: toolCallLog.length },
        });
        return {
          agent: agent.name,
          output: response.content.trim(),
          iterations,
          toolCalls: toolCallLog,
          stoppedBecause: awaitingApproval ? 'awaiting_approval' : 'complete',
        };
      }

      messages.push({
        role: 'assistant',
        content: response.content,
        toolCalls: response.toolCalls,
      });

      for (const call of response.toolCalls) {
        const outcome = await executor.execute({
          tool: call.name,
          args: call.arguments,
          agent,
          userId: ctx.userId,
          conversationId: ctx.conversationId,
          ...(ctx.signal ? { signal: ctx.signal } : {}),
        });

        if (outcome.status === 'awaiting_approval') awaitingApproval = true;
        toolCallLog.push({ name: call.name, ok: outcome.status === 'executed' });

        messages.push({
          role: 'tool',
          toolCallId: call.id,
          name: call.name,
          content: outcome.message,
        });
      }
    }

    // Budget exhausted: ask for a final answer with no tools available, so the
    // agent reports what it actually established instead of looping forever.
    let closing = '';
    try {
      const response = await provider.chat({
        messages: [
          ...messages,
          {
            role: 'user',
            content:
              'You have reached your step limit. Summarise what you established, what you did, and what remains — accurately, with no new tool calls.',
          },
        ],
        temperature: 0.2,
        ...(ctx.signal ? { signal: ctx.signal } : {}),
      });
      closing = response.content.trim();
    } catch (error) {
      closing = `${agent.title} reached its step limit and the closing summary also failed: ${(error as Error).message}`;
    }

    events.emit({
      type: 'AGENT_RESULT',
      userId: ctx.userId,
      conversationId: ctx.conversationId,
      agent: agent.name,
      summary: `${agent.title} stopped at its ${agent.maxIterations}-step limit.`,
      data: { iterations, toolCalls: toolCallLog.length },
    });

    return {
      agent: agent.name,
      output: closing,
      iterations,
      toolCalls: toolCallLog,
      stoppedBecause: 'max_iterations',
    };
  }

  private buildSystemPrompt(agent: AgentDefinition, ctx: AgentRunContext): string {
    const parts = [agent.systemPrompt];

    const toolNames = this.deps.registry.listForAgent(agent).map((tool) => tool.name);
    parts.push(
      toolNames.length > 0
        ? `Tools available to you: ${toolNames.join(', ')}. You have no other capabilities.`
        : 'You have no tools available. Answer from the information you were given.',
    );

    if (ctx.memories && ctx.memories.length > 0) {
      const lines = ctx.memories.map((memory) => `- [${memory.type}] ${memory.content}`);
      parts.push(`Known context about the user:\n${lines.join('\n')}`);
    }
    if (ctx.briefing) parts.push(`Briefing from JARVIS:\n${ctx.briefing}`);

    return parts.join('\n\n');
  }
}
