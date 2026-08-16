/**
 * The orchestrator.
 *
 * Given a user message it decides — via the model, with the tools and memory it
 * was handed — whether to answer directly, retrieve, call a tool, write a
 * memory, delegate to an agent, or stop and ask for approval. It owns the
 * conversation loop; the executor owns permission; the registry owns tools.
 *
 * Deliberately a plain loop. Anyone reading this file can see the whole control
 * flow, which is the property that matters most in a system that is allowed to
 * act on your behalf.
 */
import type {
  AgentDefinition,
  ApprovalRequest,
  ChatMessage,
  JarvisEvent,
  MemorySearchResult,
  StoredMessage,
} from '@jarvis/shared';
import { EventBus, truncate } from '@jarvis/shared';
import type { Store } from '@jarvis/memory';
import type { ModelProvider, ModelRouter, CapabilityName } from '@jarvis/providers';
import { NoCapableModelError } from '@jarvis/providers';
import type { ToolRegistry } from '@jarvis/tools';
import type { ToolExecutor } from './executor.ts';
import { TurnRegistry, type TurnHandle, type TurnOutcome } from './turns.ts';

export interface OrchestratorDeps {
  provider: ModelProvider;
  /** Owns the cancellation boundary for every turn this orchestrator runs. */
  turns: TurnRegistry;
  /** Chooses a model by capability. Falls back to `provider` when absent. */
  router?: ModelRouter;
  registry: ToolRegistry;
  executor: ToolExecutor;
  store: Store;
  events: EventBus;
  jarvisAgent: AgentDefinition;
  /** How many past messages to replay into the model. */
  historyLimit?: number;
}

export interface TurnResult {
  /** Correlates every event, audit row and tool call produced by this turn. */
  turnId: string;
  /** How the turn ended. Cancellation is never reported as a failure. */
  outcome: TurnOutcome;
  conversationId: string;
  reply: string;
  messages: StoredMessage[];
  toolCalls: { name: string; status: string }[];
  pendingApprovals: ApprovalRequest[];
  events: JarvisEvent[];
  model: string;
  provider: string;
  iterations: number;
  error?: string;
}

const MAX_HISTORY = 40;

export class Orchestrator {
  private readonly historyLimit: number;

  constructor(private readonly deps: OrchestratorDeps) {
    this.historyLimit = deps.historyLimit ?? MAX_HISTORY;
  }

  /** Route by capability, or fall back to the single configured provider. */
  private route(required: CapabilityName[]) {
    if (this.deps.router) return this.deps.router.select(required);
    return {
      provider: this.deps.provider,
      providerId: this.deps.provider.id,
      model: this.deps.provider.model,
      requested: required,
      fallbackUsed: false,
      reason: 'no router configured; using the single configured provider',
    };
  }

  /**
   * Persist what the call cost and how long it took.
   *
   * These numbers were already being measured and thrown away. Token counts
   * stay null when a provider does not report them — a zero would read as
   * "free", which is a different claim.
   *
   * Attribution comes from the provider that actually served the call, not from
   * the routing slot it was selected under. The two are the same in a normal
   * deployment; when they diverge the row should name what really ran, and the
   * slot survives in `routingReason` either way.
   */
  private recordUsage(
    turn: TurnHandle,
    userId: string,
    agent: string,
    route: {
      provider: { id: string; model: string };
      requested: string[];
      fallbackUsed: boolean;
      reason: string;
    },
    startedAt: number,
    response: { usage?: { promptTokens: number; completionTokens: number }; latencyMs?: number } | null,
    outcome: 'ok' | 'error' | 'cancelled',
    error?: string,
  ): void {
    try {
      this.deps.store.usage.record({
        turnId: turn.turnId,
        userId,
        agent,
        provider: route.provider.id,
        model: route.provider.model,
        requested: route.requested,
        fallbackUsed: route.fallbackUsed,
        routingReason: route.reason,
        inputTokens: response?.usage?.promptTokens ?? null,
        outputTokens: response?.usage?.completionTokens ?? null,
        latencyMs: response?.latencyMs ?? Date.now() - startedAt,
        outcome,
        ...(error ? { error } : {}),
      });
    } catch {
      /* accounting must never break the request path */
    }
  }

  /** Memories relevant to a piece of text. Shared with the delegation path. */
  retrieveContext(userId: string, text: string, limit = 6): MemorySearchResult[] {
    const relevant = this.deps.store.memories.search(userId, text, { limit });
    if (relevant.length > 0) return relevant;

    // Nothing matched lexically: fall back to standing instructions and
    // preferences, which should influence every answer.
    const standing = [
      ...this.deps.store.memories.list(userId, { type: 'instruction', limit: 3 }),
      ...this.deps.store.memories.list(userId, { type: 'preference', limit: 3 }),
    ];
    return standing.map((memory) => ({ ...memory, score: memory.importance }));
  }

  async handleMessage(input: {
    userId: string;
    conversationId?: string | null;
    text: string;
    /** Supply to reuse a turn id (the API allocates one so it can be cancelled). */
    turnId?: string;
  }): Promise<TurnResult> {
    const { store, events, provider, registry, executor, jarvisAgent } = this.deps;
    const { userId, text } = input;

    store.users.ensure(userId);
    const conversation = input.conversationId
      ? (store.conversations.get(input.conversationId) ?? store.conversations.create(userId))
      : store.conversations.create(userId);
    const conversationId = conversation.id;

    // One controller for the whole turn. Every model call and tool call below
    // receives its signal, and `finally` releases it on every exit path.
    const turn = this.deps.turns.begin({
      userId,
      conversationId,
      ...(input.turnId ? { turnId: input.turnId } : {}),
    });

    const collected: JarvisEvent[] = [];
    const unsubscribe = events.subscribe((event) => {
      if (event.conversationId === conversationId) collected.push(event);
    });

    try {
      store.messages.append(conversationId, { role: 'user', content: text });
      store.conversations.autoTitle(conversationId, text);
      store.conversations.touch(conversationId);
      events.emit({
        type: 'USER_MESSAGE',
        userId,
        turnId: turn.turnId,
        conversationId,
        agent: 'user',
        summary: truncate(text, 140),
        data: {},
      });

      if (!provider.isAvailable()) {
        const reason = provider.status().reason ?? 'the configured model provider is unavailable';
        const reply =
          `I can't answer that: the ${provider.id} model provider is not configured.\n\n${reason}\n\n` +
          `Set the matching values in .env and restart. Everything else — memory, tools, approvals, audit — is running.`;
        store.messages.append(conversationId, { role: 'assistant', content: reply, agent: 'jarvis' });
        events.emit({
          type: 'ERROR',
          userId,
          turnId: turn.turnId,
          conversationId,
          agent: 'jarvis',
          summary: `Model provider unavailable: ${provider.id}`,
          data: { reason },
        });
        return this.result(turn, 'failed', conversationId, reply, collected, [], 0, reason);
      }

      const memories = this.retrieveContext(userId, text);
      if (memories.length > 0) {
        events.emit({
          type: 'MEMORY_READ',
          userId,
          turnId: turn.turnId,
          conversationId,
          agent: 'jarvis',
          summary: `Recalled ${memories.length} relevant memor${memories.length === 1 ? 'y' : 'ies'}.`,
          data: { ids: memories.map((memory) => memory.id) },
        });
      }

      const messages: ChatMessage[] = [
        { role: 'system', content: this.buildSystemPrompt(memories) },
        ...this.history(conversationId),
      ];
      const specs = registry.specsForAgent(jarvisAgent);
      const toolCalls: { name: string; status: string }[] = [];

      let iterations = 0;
      let reply = '';

      while (iterations < jarvisAgent.maxIterations) {
        // Cancelled between steps: the next model call never starts.
        if (turn.signal.aborted) {
          return this.cancelled(turn, conversationId, collected, toolCalls, iterations);
        }
        iterations += 1;

        // Capability-based routing: offering tools means the model must be
        // able to call them. Nothing here inspects what the user asked for.
        const required: CapabilityName[] = specs.length > 0 ? ['toolUse'] : [];
        let route;
        try {
          route = this.route(required);
        } catch (error) {
          if (error instanceof NoCapableModelError) {
            const reply = `I can't answer that: ${error.message}`;
            store.messages.append(conversationId, { role: 'assistant', content: reply, agent: 'jarvis' });
            events.emit({
              type: 'ERROR',
              userId,
              turnId: turn.turnId,
              conversationId,
              agent: 'jarvis',
              summary: `No capable model for ${required.join(' + ')}`,
              data: { kind: 'provider', requested: required },
            });
            return this.result(turn, 'failed', conversationId, reply, collected, toolCalls, iterations, error.message);
          }
          throw error;
        }

        const startedAt = Date.now();
        let response;
        try {
          response = await route.provider.chat({
            messages,
            tools: specs,
            temperature: 0.4,
            signal: turn.signal,
          });
          this.recordUsage(turn, userId, 'jarvis', route, startedAt, response, 'ok');
        } catch (error) {
          this.recordUsage(
            turn,
            userId,
            'jarvis',
            route,
            startedAt,
            null,
            turn.signal.aborted ? 'cancelled' : 'error',
            (error as Error).message,
          );
          // A model call that failed because the turn was cancelled is a
          // cancellation, not a provider failure.
          if (turn.signal.aborted) {
            return this.cancelled(turn, conversationId, collected, toolCalls, iterations);
          }
          const message = (error as Error).message;
          events.emit({
            type: 'ERROR',
            userId,
            turnId: turn.turnId,
            conversationId,
            agent: 'jarvis',
            summary: `Model call failed: ${truncate(message, 140)}`,
            data: { error: message },
          });
          reply = `The model call failed: ${message}\n\nNothing was executed. Check the provider configuration in Settings and try again.`;
          store.messages.append(conversationId, { role: 'assistant', content: reply, agent: 'jarvis' });
          return this.result(turn, 'failed', conversationId, reply, collected, toolCalls, iterations, message);
        }

        if (response.toolCalls.length === 0) {
          reply = response.content.trim() || '(the model returned an empty response)';
          store.messages.append(conversationId, { role: 'assistant', content: reply, agent: 'jarvis' });
          events.emit({
            type: 'MODEL_RESPONSE',
            userId,
            turnId: turn.turnId,
            conversationId,
            agent: 'jarvis',
            summary: truncate(reply, 140),
            data: {
              model: response.model,
              provider: response.providerId,
              latencyMs: response.latencyMs,
              usage: response.usage,
            },
          });
          break;
        }

        messages.push({
          role: 'assistant',
          content: response.content,
          toolCalls: response.toolCalls,
        });
        store.messages.append(conversationId, {
          role: 'assistant',
          content: response.content,
          toolCalls: response.toolCalls,
          agent: 'jarvis',
        });

        for (const call of response.toolCalls) {
          // Cancelled between tool calls: the remaining calls never start.
          if (turn.signal.aborted) {
            return this.cancelled(turn, conversationId, collected, toolCalls, iterations);
          }

          const outcome = await executor.execute({
            tool: call.name,
            args: call.arguments,
            agent: jarvisAgent,
            userId,
            conversationId,
            turn: turn.context,
          });
          toolCalls.push({ name: call.name, status: outcome.status });

          const toolMessage: ChatMessage = {
            role: 'tool',
            toolCallId: call.id,
            name: call.name,
            content: outcome.message,
          };
          messages.push(toolMessage);
          store.messages.append(conversationId, {
            role: 'tool',
            content: outcome.message,
            toolCallId: call.id,
            name: call.name,
            agent: 'jarvis',
          });
        }
      }

      if (!reply) {
        reply =
          'I reached my step limit for this turn without producing an answer. Ask me to continue and I will pick up from here.';
        store.messages.append(conversationId, { role: 'assistant', content: reply, agent: 'jarvis' });
      }

      store.conversations.touch(conversationId);
      return this.result(turn, 'completed', conversationId, reply, collected, toolCalls, iterations);
    } finally {
      unsubscribe();
      // Releases the controller on completion, error and cancellation alike.
      turn.end();
    }
  }

  /**
   * Continue a conversation after a human decided on a pending approval.
   *
   * The executed (or refused) action is written into the conversation as an
   * explicit note and the model is asked to respond to it. The note is factual
   * and generated here, not by the model, so JARVIS cannot claim an outcome
   * that did not occur.
   */
  async continueAfterApproval(input: {
    userId: string;
    conversationId: string;
    /** Internal instruction to the model. Never shown to the user verbatim. */
    note: string;
    /** User-facing sentence used if the model produces nothing usable. */
    fallback: string;
    /** The turn the approval decision belongs to, so the follow-up correlates to it. */
    turnId?: string;
    /** The turn that originally requested the approval. */
    parentTurnId?: string | null;
  }): Promise<TurnResult> {
    const { store, events, provider } = this.deps;
    const { userId, conversationId, note, fallback } = input;

    const turn = this.deps.turns.begin({
      userId,
      conversationId,
      ...(input.turnId ? { turnId: input.turnId } : {}),
      ...(input.parentTurnId ? { parentTurnId: input.parentTurnId } : {}),
    });

    const collected: JarvisEvent[] = [];
    const unsubscribe = events.subscribe((event) => {
      if (event.conversationId === conversationId) collected.push(event);
    });

    try {
      // Stored as `system` so the transcript never shows an internal
      // instruction as something the user said. history() re-frames it for
      // the provider; the UI filters system messages out entirely.
      store.messages.append(conversationId, { role: 'system', content: note, agent: 'system' });

      if (!provider.isAvailable()) {
        return this.result(
          turn,
          'failed',
          conversationId,
          fallback,
          collected,
          [],
          0,
          'model provider unavailable',
        );
      }

      const memories = this.retrieveContext(userId, note);
      const messages: ChatMessage[] = [
        { role: 'system', content: this.buildSystemPrompt(memories) },
        ...this.history(conversationId),
      ];

      let reply: string;
      try {
        // No tools on this turn: the decision has already been executed (or
        // refused), and the model's only job is to report it in prose. Handing
        // it tools here invites it to redo the action it was just told about.
        const response = await provider.chat({
          messages,
          temperature: 0.3,
          signal: turn.signal,
        });
        reply = response.content.trim() || fallback;
      } catch (error) {
        reply = `${fallback}\n\n(The follow-up model call failed: ${(error as Error).message})`;
      }

      store.messages.append(conversationId, { role: 'assistant', content: reply, agent: 'jarvis' });
      events.emit({
        type: 'MODEL_RESPONSE',
        userId,
        turnId: turn.turnId,
        parentTurnId: turn.parentTurnId,
        conversationId,
        agent: 'jarvis',
        summary: truncate(reply, 140),
        data: { afterApproval: true },
      });
      store.conversations.touch(conversationId);

      return this.result(turn, 'completed', conversationId, reply, collected, [], 1);
    } finally {
      unsubscribe();
      turn.end();
    }
  }

  private result(
    turn: TurnHandle,
    outcome: TurnOutcome,
    conversationId: string,
    reply: string,
    events: JarvisEvent[],
    toolCalls: { name: string; status: string }[],
    iterations: number,
    error?: string,
  ): TurnResult {
    const result: TurnResult = {
      turnId: turn.turnId,
      outcome,
      conversationId,
      reply,
      messages: this.deps.store.messages.list(conversationId),
      toolCalls,
      pendingApprovals: this.deps.store.approvals
        .listPending(this.deps.store.conversations.get(conversationId)?.userId ?? '')
        .filter((approval) => approval.conversationId === conversationId),
      events,
      model: this.deps.provider.model,
      provider: this.deps.provider.id,
      iterations,
    };
    if (error) result.error = error;
    return result;
  }

  /**
   * Stop a cancelled turn.
   *
   * Reported as `cancelled`, never as an error: the user asked for this, and a
   * UI that shows "something failed" after a deliberate cancel is lying about
   * what happened. The partial reply is recorded so the transcript reflects
   * where the turn actually stopped.
   */
  private cancelled(
    turn: TurnHandle,
    conversationId: string,
    events: JarvisEvent[],
    toolCalls: { name: string; status: string }[],
    iterations: number,
  ): TurnResult {
    const reply = `Cancelled. I stopped this turn after ${iterations} step${
      iterations === 1 ? '' : 's'
    }${toolCalls.length > 0 ? ` and ${toolCalls.length} tool call${toolCalls.length === 1 ? '' : 's'}` : ''}. Nothing further was executed.`;

    this.deps.store.messages.append(conversationId, {
      role: 'assistant',
      content: reply,
      agent: 'jarvis',
    });
    this.deps.events.emit({
      type: 'TURN_CANCELLED',
      userId: turn.userId,
      turnId: turn.turnId,
      conversationId,
      agent: 'jarvis',
      summary: turn.reason ?? 'Turn cancelled.',
      data: { iterations, toolCalls: toolCalls.length, reason: turn.reason },
    });

    return this.result(turn, 'cancelled', conversationId, reply, events, toolCalls, iterations);
  }

  /** Conversation history, trimmed and converted to provider-neutral messages. */
  private history(conversationId: string): ChatMessage[] {
    const stored = this.deps.store.messages.list(conversationId);
    const recent = stored.slice(-this.historyLimit);

    // A `tool` message whose originating assistant call was trimmed away would
    // be rejected by strict providers; drop such orphans.
    const knownCallIds = new Set<string>();
    for (const message of recent) {
      for (const call of message.toolCalls ?? []) knownCallIds.add(call.id);
    }

    const out: ChatMessage[] = [];
    for (const message of recent) {
      if (message.role === 'tool' && (!message.toolCallId || !knownCallIds.has(message.toolCallId))) {
        continue;
      }
      // Mid-conversation system notes are re-framed as user turns: providers
      // treat `system` as a single top-level instruction, so a note left in
      // place would either be hoisted out of order or ignored.
      if (message.role === 'system') {
        out.push({
          role: 'user',
          content: `[JARVIS system note — not written by the user] ${message.content}`,
        });
        continue;
      }
      const chat: ChatMessage = { role: message.role, content: message.content };
      if (message.toolCalls) chat.toolCalls = message.toolCalls;
      if (message.toolCallId) chat.toolCallId = message.toolCallId;
      if (message.name) chat.name = message.name;
      out.push(chat);
    }
    return out;
  }

  private buildSystemPrompt(memories: MemorySearchResult[]): string {
    const parts = [this.deps.jarvisAgent.systemPrompt];

    const toolNames = this.deps.registry.listForAgent(this.deps.jarvisAgent).map((tool) => tool.name);
    parts.push(`Tools available: ${toolNames.join(', ')}.`);

    if (memories.length > 0) {
      parts.push(
        `What you already know about the user (from long-term memory):\n${memories
          .map((memory) => `- [${memory.type}] ${memory.content}`)
          .join('\n')}`,
      );
    } else {
      parts.push('Long-term memory currently holds nothing relevant to this message.');
    }

    return parts.join('\n\n');
  }
}
