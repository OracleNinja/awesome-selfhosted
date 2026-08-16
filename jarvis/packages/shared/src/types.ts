/**
 * Core domain types for JARVIS.
 *
 * These are deliberately provider-agnostic: nothing in here knows about
 * NVIDIA, Anthropic or OpenAI. Providers adapt themselves to these types,
 * never the other way around.
 */

export type ISODate = string;

// ---------------------------------------------------------------------------
// Security
// ---------------------------------------------------------------------------

export const RISK_LEVELS = ['READ', 'WRITE', 'EXTERNAL_ACTION', 'DESTRUCTIVE'] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

/** Ordering used for "at least this risky" comparisons. */
export const RISK_ORDER: Record<RiskLevel, number> = {
  READ: 0,
  WRITE: 1,
  EXTERNAL_ACTION: 2,
  DESTRUCTIVE: 3,
};

export type ApprovalState = 'pending' | 'approved' | 'denied' | 'expired';

// ---------------------------------------------------------------------------
// Conversation
// ---------------------------------------------------------------------------

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ChatMessage {
  role: MessageRole;
  content: string;
  /** Present on assistant messages that requested tools. */
  toolCalls?: ToolCall[];
  /** Present on tool messages: which call this is answering. */
  toolCallId?: string;
  name?: string;
}

export interface StoredMessage extends ChatMessage {
  id: string;
  conversationId: string;
  agent: string | null;
  createdAt: ISODate;
}

export interface Conversation {
  id: string;
  userId: string;
  title: string;
  createdAt: ISODate;
  updatedAt: ISODate;
}

export interface User {
  id: string;
  name: string;
  createdAt: ISODate;
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export const MEMORY_TYPES = [
  'preference',
  'fact',
  'goal',
  'project',
  'person',
  'instruction',
  'temporary',
] as const;
export type MemoryType = (typeof MEMORY_TYPES)[number];

export interface Memory {
  id: string;
  userId: string;
  type: MemoryType;
  content: string;
  /** Where it came from: "user", "agent:scout", "tool:memory_write", ... */
  source: string;
  /** 0..1 — how sure JARVIS is that this is true. */
  confidence: number;
  /** 0..1 — how much this should influence future reasoning. */
  importance: number;
  tags: string[];
  createdAt: ISODate;
  updatedAt: ISODate;
  /** Set for `temporary` memories; expired rows are filtered out on read. */
  expiresAt: ISODate | null;
}

export interface MemorySearchResult extends Memory {
  score: number;
}

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled';
export type TaskPriority = 'low' | 'normal' | 'high' | 'urgent';

export interface Task {
  id: string;
  userId: string;
  title: string;
  detail: string;
  status: TaskStatus;
  priority: TaskPriority;
  assignedAgent: string | null;
  dueAt: ISODate | null;
  createdAt: ISODate;
  updatedAt: ISODate;
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

export interface JsonSchema {
  type?: 'object' | 'string' | 'number' | 'integer' | 'boolean' | 'array' | 'null';
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  additionalProperties?: boolean;
}

export interface ToolContext {
  userId: string;
  conversationId: string | null;
  /** Which agent (or "jarvis") is invoking the tool. */
  agent: string;
  signal?: AbortSignal;
}

export interface ToolResult {
  ok: boolean;
  /** Human/model readable summary. Always present. */
  summary: string;
  /** Structured payload handed back to the model as JSON. */
  data?: unknown;
  error?: string;
}

export interface ToolDefinition {
  name: string;
  description: string;
  inputSchema: JsonSchema;
  risk: RiskLevel;
  /**
   * Tool-declared approval requirement. The effective requirement is
   * `requiresApproval || policy.requiresApprovalFor(risk)` — a tool can opt in
   * to approval but can never opt out of the policy.
   */
  requiresApproval: boolean;
  /** Tools an agent may not use are filtered out before the model ever sees them. */
  execute(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult>;
}

/** Serialisable view of a tool — safe to send to the browser. */
export interface ToolInfo {
  name: string;
  description: string;
  inputSchema: JsonSchema;
  risk: RiskLevel;
  requiresApproval: boolean;
  available: boolean;
  unavailableReason?: string;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export type AgentName = 'jarvis' | 'scout' | 'operator' | 'advisor' | 'developer';

export interface AgentDefinition {
  name: AgentName;
  title: string;
  purpose: string;
  systemPrompt: string;
  /** Tool names this agent may call. The single entry `"*"` means "every registered tool". */
  allowedTools: string[];
  /** Hard ceiling on tool risk, regardless of the tool's own declaration. */
  maxRisk: RiskLevel;
  /** Agents that may never mutate anything are marked read-only. */
  readOnly: boolean;
  maxIterations: number;
}

export interface AgentRunResult {
  agent: AgentName;
  output: string;
  iterations: number;
  toolCalls: { name: string; ok: boolean }[];
  stoppedBecause: 'complete' | 'max_iterations' | 'awaiting_approval' | 'error';
  error?: string;
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

export interface ProviderStatus {
  id: string;
  kind: 'model' | 'stt' | 'tts' | 'image' | 'image_edit' | 'video' | 'vision' | 'search';
  available: boolean;
  /** Present when `available` is false — always explain *why*. */
  reason?: string;
  model?: string;
  /** Never contains secrets — host only. */
  endpoint?: string;
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

export const EVENT_TYPES = [
  'USER_MESSAGE',
  'MODEL_RESPONSE',
  'TOOL_REQUEST',
  'TOOL_RESULT',
  'MEMORY_WRITE',
  'MEMORY_READ',
  'AGENT_DELEGATION',
  'AGENT_RESULT',
  'APPROVAL_REQUEST',
  'APPROVAL_RESOLVED',
  'ACTION_EXECUTED',
  'ERROR',
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface JarvisEvent {
  id: string;
  type: EventType;
  conversationId: string | null;
  userId: string;
  agent: string;
  /** One-line human summary, shown in the activity feed. */
  summary: string;
  data: Record<string, unknown>;
  createdAt: ISODate;
}

// ---------------------------------------------------------------------------
// Approvals & audit
// ---------------------------------------------------------------------------

export interface ApprovalRequest {
  id: string;
  userId: string;
  conversationId: string | null;
  agent: string;
  tool: string;
  description: string;
  risk: RiskLevel;
  arguments: Record<string, unknown>;
  state: ApprovalState;
  createdAt: ISODate;
  resolvedAt: ISODate | null;
  expiresAt: ISODate;
  decidedBy: string | null;
  note: string | null;
}

export interface AuditEvent {
  id: string;
  timestamp: ISODate;
  userId: string;
  agent: string;
  tool: string;
  arguments: Record<string, unknown>;
  approvalState: ApprovalState | 'not_required';
  approvalId: string | null;
  result: string | null;
  error: string | null;
  durationMs: number;
  risk: RiskLevel;
}
