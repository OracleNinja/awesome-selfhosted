/**
 * The tool executor — the single choke point for every action JARVIS takes.
 *
 * Order of operations, and none of it is skippable:
 *
 *   1. resolve the tool        → unknown tools fail closed
 *   2. agent permission        → the agent's declared ceiling, checked in code
 *   3. argument validation     → against the tool's published JSON Schema
 *   4. approval gate           → EXTERNAL_ACTION / DESTRUCTIVE stop here
 *   5. execute                 → timed, with errors caught
 *   6. audit                   → always, including refusals and failures
 *
 * Every path writes an audit row. A tool that was never allowed to run leaves
 * as much evidence as one that did.
 */
import type {
  AgentDefinition,
  ApprovalRequest,
  JsonSchema,
  ToolDefinition,
  ToolResult,
} from '@jarvis/shared';
import { EventBus, id, redact, truncate, validateInput } from '@jarvis/shared';
import type { Store } from '@jarvis/memory';
import type { PermissionPolicy } from '@jarvis/security';
import type { ToolRegistry } from '@jarvis/tools';
import type { ExecutionOutcome } from '@jarvis/agents';
import {
  DEFAULT_TOOL_TIMEOUT_MS,
  isAbortError,
  linkTimeout,
  ToolTimeoutError,
  type TurnContext,
} from './turns.ts';

export interface ExecuteRequest {
  tool: string;
  args: Record<string, unknown>;
  agent: AgentDefinition;
  userId: string;
  conversationId: string | null;
  /** The turn this call belongs to: its id for correlation, its signal for cancellation. */
  turn: TurnContext;
}

export interface ToolExecutorDeps {
  registry: ToolRegistry;
  policy: PermissionPolicy;
  store: Store;
  events: EventBus;
  approvalTimeoutSeconds: number;
}

/** A short human sentence describing what a call will do, shown on the approval card. */
export function describeAction(tool: ToolDefinition, args: Record<string, unknown>): string {
  const rendered = Object.entries(args)
    .map(([key, value]) => `${key}=${truncate(JSON.stringify(value) ?? String(value), 60)}`)
    .join(', ');
  return rendered ? `${tool.name}(${rendered})` : `${tool.name}()`;
}

export class ToolExecutor {
  constructor(private readonly deps: ToolExecutorDeps) {}

  async execute(request: ExecuteRequest): Promise<ExecutionOutcome> {
    const { registry, policy, store, events } = this.deps;
    const { tool: toolName, agent, userId, conversationId } = request;
    const startedAt = Date.now();
    // Correlates TOOL_REQUEST with its TOOL_RESULT. Without it, a client
    // watching the stream has to guess which result belongs to which call when
    // the same tool is invoked twice in one turn.
    const callId = id('exec');
    const { turnId, signal: turnSignal, parentTurnId } = request.turn;

    // 0. Already cancelled? Then this call never starts. Checked before the
    //    tool is resolved so a cancelled turn cannot create an approval or
    //    reach an execute() body.
    if (turnSignal.aborted) {
      const message = `Cancelled: turn ${turnId} was cancelled before "${toolName}" started. It did not run.`;
      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: toolName,
        arguments: request.args,
        approvalState: 'not_required',
        error: 'turn cancelled before execution',
        durationMs: 0,
        risk: registry.get(toolName)?.risk ?? 'READ',
        conversationId,
      });
      return { status: 'cancelled', result: null, message };
    }

    // 1. Resolve.
    const tool = registry.get(toolName);
    if (!tool) {
      const message = `Tool "${toolName}" does not exist. Available: ${registry
        .listForAgent(agent)
        .map((t) => t.name)
        .join(', ')}.`;
      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: toolName,
        arguments: request.args,
        approvalState: 'not_required',
        error: message,
        durationMs: Date.now() - startedAt,
        risk: 'READ',
        conversationId,
      });
      events.emit({
        type: 'ERROR',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: `Unknown tool: ${toolName}`,
        data: { tool: toolName },
      });
      return { status: 'error', result: null, message };
    }

    // 1b. Disabled? A capability whose source is unavailable (a disconnected
    //     MCP server, an operator switch) is refused here, at the same choke
    //     point as everything else — not by hiding it from the model and hoping.
    const disabled = registry.disabledReason(tool.name);
    if (disabled) {
      const message = `Refused: ${disabled}. This action did not run.`;
      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: tool.name,
        arguments: request.args,
        approvalState: 'denied',
        error: disabled,
        durationMs: Date.now() - startedAt,
        risk: tool.risk,
        conversationId,
      });
      events.emit({
        type: 'ERROR',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: `Capability unavailable: ${tool.name}`,
        data: { kind: 'capability', tool: tool.name, reason: disabled },
      });
      return { status: 'denied', result: null, message };
    }

    events.emit({
      type: 'TOOL_REQUEST',
      userId,
      turnId,
      parentTurnId,
      conversationId,
      agent: agent.name,
      summary: `${agent.name} → ${tool.name}`,
      data: { callId, tool: tool.name, risk: tool.risk, arguments: redact(request.args) },
    });

    // 2. Permission.
    const permission = policy.canAgentUseTool(agent, tool);
    if (!permission.allowed) {
      const message = `Refused: ${permission.reason}. This action did not run.`;
      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: tool.name,
        arguments: request.args,
        approvalState: 'denied',
        error: permission.reason,
        durationMs: Date.now() - startedAt,
        risk: tool.risk,
        conversationId,
      });
      events.emit({
        type: 'ERROR',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: `Permission denied: ${tool.name}`,
        data: { kind: 'permission', tool: tool.name, reason: permission.reason },
      });
      return { status: 'denied', result: null, message };
    }

    // 3. Validate.
    const validation = validateInput(request.args, tool.inputSchema as JsonSchema);
    if (!validation.ok) {
      const message = `Invalid arguments for ${tool.name}: ${validation.errors.join('; ')}. The tool did not run — fix the arguments and try again.`;
      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: tool.name,
        arguments: request.args,
        approvalState: 'not_required',
        error: validation.errors.join('; '),
        durationMs: Date.now() - startedAt,
        risk: tool.risk,
        conversationId,
      });
      events.emit({
        type: 'ERROR',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: `Invalid arguments for ${tool.name}`,
        data: { kind: 'tool', tool: tool.name, errors: validation.errors },
      });
      return { status: 'error', result: null, message };
    }

    // 4. Approval.
    const approval = policy.approvalFor(tool);
    if (approval.required) {
      const request_ = store.approvals.create({
        userId,
        conversationId,
        agent: agent.name,
        tool: tool.name,
        description: describeAction(tool, validation.value),
        risk: tool.risk,
        arguments: validation.value,
        timeoutSeconds: this.deps.approvalTimeoutSeconds,
      });

      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: tool.name,
        arguments: validation.value,
        approvalState: 'pending',
        approvalId: request_.id,
        result: 'awaiting human approval',
        durationMs: Date.now() - startedAt,
        risk: tool.risk,
        conversationId,
      });

      events.emit({
        type: 'APPROVAL_REQUEST',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: `Approval required: ${request_.description}`,
        data: {
          callId,
          approvalId: request_.id,
          tool: tool.name,
          risk: tool.risk,
          arguments: redact(validation.value),
          expiresAt: request_.expiresAt,
        },
      });

      return {
        status: 'awaiting_approval',
        result: null,
        approvalId: request_.id,
        message:
          `APPROVAL REQUIRED. "${tool.name}" is classified ${tool.risk} and has NOT been executed. ` +
          `Request ${request_.id} is waiting for the user's decision. ` +
          `Tell the user what you are asking permission to do and stop. Do not retry and do not describe this as done.`,
      };
    }

    // 5. Execute.
    return this.runTool(tool, validation.value, request, startedAt, null, callId);
  }

  /**
   * Execute a tool call that a human has approved.
   *
   * Re-checks state at execution time: only a request that is currently
   * `approved` runs. Expired, denied or already-resolved requests are refused,
   * so an approval cannot be replayed.
   */
  async executeApproved(
    approval: ApprovalRequest,
    agent: AgentDefinition,
    turn: TurnContext,
  ): Promise<ExecutionOutcome> {
    const { registry, store, events } = this.deps;
    const startedAt = Date.now();

    if (approval.state !== 'approved') {
      const message = `Approval ${approval.id} is ${approval.state}; the action was not executed.`;
      store.audit.record({
        userId: approval.userId,
        turnId: turn.turnId,
        agent: approval.agent,
        tool: approval.tool,
        arguments: approval.arguments,
        approvalState: approval.state,
        approvalId: approval.id,
        error: message,
        durationMs: 0,
        risk: approval.risk,
        conversationId: approval.conversationId,
      });
      return { status: 'denied', result: null, approvalId: approval.id, message };
    }

    const tool = registry.get(approval.tool);
    if (!tool) {
      const message = `Tool "${approval.tool}" is no longer registered; the approved action was not executed.`;
      events.emit({
        type: 'ERROR',
        userId: approval.userId,
        turnId: turn.turnId,
        conversationId: approval.conversationId,
        agent: approval.agent,
        summary: message,
        data: { approvalId: approval.id },
      });
      return { status: 'error', result: null, approvalId: approval.id, message };
    }

    return this.runTool(
      tool,
      approval.arguments,
      {
        tool: tool.name,
        args: approval.arguments,
        agent,
        userId: approval.userId,
        conversationId: approval.conversationId,
        turn,
      },
      startedAt,
      approval.id,
      id('exec'),
    );
  }

  /**
   * Run the tool inside its own abort boundary.
   *
   * The boundary is linked to the turn, so a cancelled turn aborts the tool,
   * and it carries the tool's own timer, so a hung tool aborts itself. Which
   * one fired is recorded at the moment it happened — that is what lets a
   * timeout be told apart from a cancellation when both land in the same
   * millisecond.
   */
  private async runTool(
    tool: ToolDefinition,
    args: Record<string, unknown>,
    request: ExecuteRequest,
    startedAt: number,
    approvalId: string | null,
    callId: string,
  ): Promise<ExecutionOutcome> {
    const { store, events } = this.deps;
    const { agent, userId, conversationId } = request;
    const { turnId, signal: turnSignal, parentTurnId } = request.turn;
    const timeoutMs = tool.timeoutMs ?? DEFAULT_TOOL_TIMEOUT_MS;

    // The timer starts when execution starts — not when the request arrived, so
    // time spent waiting on validation or an approval is not charged to the tool.
    const boundary = linkTimeout(turnSignal, timeoutMs, tool.name);
    const approvalState = approvalId ? ('approved' as const) : ('not_required' as const);

    let result: ToolResult;
    try {
      const execution = tool.execute(args, {
        userId,
        conversationId,
        agent: agent.name,
        turnId,
        signal: boundary.signal,
      });

      // A tool is *asked* to stop via its signal, but a tool that ignores it
      // must still not hold the turn open. Racing the boundary means the
      // executor stops waiting either way.
      //
      // Note the honest limit: JavaScript cannot kill a running promise. A tool
      // that ignores cancellation may still finish its work in the background —
      // its result is simply discarded, and the late rejection is swallowed
      // below so it cannot surface as an unhandled rejection.
      execution.catch(() => undefined);

      const aborted = new Promise<never>((_resolve, reject) => {
        if (boundary.signal.aborted) {
          reject(boundary.signal.reason);
          return;
        }
        boundary.signal.addEventListener('abort', () => reject(boundary.signal.reason), {
          once: true,
        });
      });

      result = await Promise.race([execution, aborted]);
    } catch (error) {
      // Classify by what actually aborted, in this order: an explicit turn
      // cancellation beats a timer that fired afterwards, and both beat a
      // generic error. `cause()` is set once, by whichever fired first.
      const cause = boundary.cause();
      const aborted = isAbortError(error) || boundary.signal.aborted;
      const status: ExecutionOutcome['status'] =
        aborted && cause === 'turn_cancelled'
          ? 'cancelled'
          : aborted && cause === 'tool_timeout'
            ? 'timeout'
            : 'error';

      const durationMs = Date.now() - startedAt;
      const detail =
        status === 'cancelled'
          ? `Cancelled: the user cancelled turn ${turnId} while "${tool.name}" was running.`
          : status === 'timeout'
            ? `Timed out: "${tool.name}" exceeded its ${timeoutMs}ms limit and was aborted.`
            : (error as Error).message;

      store.audit.record({
        userId,
        turnId,
        parentTurnId,
        agent: agent.name,
        tool: tool.name,
        arguments: args,
        approvalState,
        approvalId,
        error: detail,
        durationMs,
        risk: tool.risk,
        conversationId,
      });

      events.emit({
        type: status === 'cancelled' ? 'TURN_CANCELLED' : 'ERROR',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary:
          status === 'cancelled'
            ? `Cancelled during ${tool.name}`
            : status === 'timeout'
              ? `${tool.name} timed out after ${timeoutMs}ms`
              : `${tool.name} threw: ${truncate(detail, 120)}`,
        data: {
          kind: status === 'timeout' ? 'timeout' : status === 'cancelled' ? 'cancelled' : 'tool',
          callId,
          tool: tool.name,
          outcome: status,
          timeoutMs: status === 'timeout' ? timeoutMs : undefined,
          durationMs,
          error: status === 'error' ? detail : undefined,
        },
      });

      return {
        status,
        result: null,
        approvalId,
        message:
          status === 'cancelled'
            ? `${detail} The action did not complete.`
            : status === 'timeout'
              ? `${detail} The action did not complete — do not assume it succeeded.`
              : `${tool.name} failed with an error: ${detail}. The action did not complete.`,
      };
    } finally {
      // Always: clears the timer and detaches the turn listener, on every path.
      boundary.dispose();
    }

    const durationMs = Date.now() - startedAt;
    store.audit.record({
      userId,
      turnId,
      parentTurnId,
      agent: agent.name,
      tool: tool.name,
      arguments: args,
      approvalState,
      approvalId,
      result: result.ok ? JSON.stringify({ summary: result.summary, data: result.data }) : null,
      error: result.ok ? null : (result.error ?? result.summary),
      durationMs,
      risk: tool.risk,
      conversationId,
    });

    events.emit({
      type: 'TOOL_RESULT',
      userId,
      turnId,
      parentTurnId,
      conversationId,
      agent: agent.name,
      summary: `${tool.name}: ${truncate(result.summary, 120)}`,
      data: {
        callId,
        tool: tool.name,
        ok: result.ok,
        outcome: result.ok ? 'completed' : 'failed',
        durationMs,
        error: result.error,
      },
    });

    if (result.ok && tool.risk !== 'READ') {
      events.emit({
        type: 'ACTION_EXECUTED',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: `${tool.risk}: ${truncate(result.summary, 120)}`,
        data: { callId, tool: tool.name, risk: tool.risk, approvalId },
      });
    }

    if (tool.name === 'memory_search' && result.ok) {
      // Retrieval is a memory operation, not just a tool call. Emitting it as
      // one lets a client show memory activity without knowing which tool
      // names happen to touch memory.
      const found = (result.data as { results?: unknown[] } | undefined)?.results?.length ?? 0;
      events.emit({
        type: 'MEMORY_READ',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: truncate(result.summary, 140),
        data: { via: 'memory_search', matches: found },
      });
    }

    if (tool.name === 'memory_write' && result.ok) {
      events.emit({
        type: 'MEMORY_WRITE',
        userId,
        turnId,
        parentTurnId,
        conversationId,
        agent: agent.name,
        summary: truncate(result.summary, 140),
        data: result.data as Record<string, unknown>,
      });
    }

    const message = result.ok
      ? JSON.stringify({ ok: true, summary: result.summary, data: result.data ?? null })
      : JSON.stringify({
          ok: false,
          summary: result.summary,
          error: result.error ?? 'unknown error',
          note: 'This action did NOT succeed. Report the failure; do not describe it as done.',
        });

    return { status: result.ok ? 'executed' : 'error', result, approvalId, message };
  }
}
