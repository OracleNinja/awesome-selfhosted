/**
 * Browser API client.
 *
 * The browser holds exactly one credential: the JARVIS API token the user
 * pasted into Settings (stored in localStorage). It never sees a provider key —
 * those stay on the server, and the server has no route that returns one.
 */


export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export type Message = StoredMessage;





export interface AgentInfo {
  name: string;
  title: string;
  purpose: string;
  maxRisk: RiskLevel;
  readOnly: boolean;
  maxIterations: number;
  tools: string[];
  runCount: number;
  lastRunAt: string | null;
}


export interface SystemStatus {
  version: string;
  ok: boolean;
  activeModelProvider: string;
  activeModel: string;
  providers: ProviderStatus[];
  database: { ok: boolean; schemaVersion: number; tables: string[] };
  tools: { total: number; requiringApproval: number };
  agents: string[];
  charterErrors: string[];
  config: {
    modelProvider: string;
    sttProvider: string;
    ttsProvider: string;
    imageProvider: string;
    videoProvider: string;
    searchProvider: string;
    approvalRequiredLevels: RiskLevel[];
    version: string;
  };
  counts: { memories: number; auditRecords?: number; auditEvents: number; pendingApprovals: number };
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  agent: string;
  tool: string;
  arguments: Record<string, unknown>;
  approvalState: string;
  result: string | null;
  error: string | null;
  durationMs: number;
  risk: RiskLevel;
}

export interface TurnResult {
  conversationId: string;
  reply: string;
  messages: Message[];
  toolCalls: { name: string; status: string }[];
  pendingApprovals: ApprovalRequest[];
  events: JarvisEvent[];
  model: string;
  provider: string;
  iterations: number;
  error?: string;
}

// Domain types come from the shared package — see runtime/types.ts. Declaring
// a second copy here is how a client and a runtime drift apart.
import type {
  ApprovalRequest,
  Memory,
  MemorySearchResult,
  ProviderStatus,
  RiskLevel,
  JarvisEvent,
  RuntimeStateSnapshot,
  StoredMessage,
  SystemTelemetry,
  ToolInfo,
} from './types';

export type { ApprovalRequest, JarvisEvent, Memory, MemorySearchResult, ProviderStatus, RiskLevel, ToolInfo };

const TOKEN_KEY = 'jarvis.apiToken';

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private browsing */
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken();
  const response = await fetch(path, {
    method,
    headers: {
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    // Most failures use the {error:{code,message}} envelope, but the approval
    // routes return a bare outcome body on 409 (`{ok,state,message}`). Reading
    // only the envelope turned "this approval already expired" into an empty
    // error in the UI.
    const error = payload?.error ?? {};
    const message = error.message ?? payload?.message ?? response.statusText ?? `HTTP ${response.status}`;
    throw new ApiError(response.status, error.code ?? payload?.state ?? 'error', message);
  }
  return payload as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>('GET', '/api/health'),
  runtimeState: () => request<RuntimeStateSnapshot>('GET', '/api/runtime/state'),
  telemetry: () => request<SystemTelemetry>('GET', '/api/system/telemetry'),
  status: () => request<SystemStatus>('GET', '/api/system/status'),
  providerCheck: () =>
    request<{ ok: boolean; detail: string; provider: string; model: string }>(
      'POST',
      '/api/system/provider-check',
      {},
    ),

  conversations: () => request<{ conversations: Conversation[] }>('GET', '/api/conversations'),
  createConversation: (title?: string) =>
    request<{ conversation: Conversation }>('POST', '/api/conversations', { title }),
  messages: (id: string) =>
    request<{ conversation: Conversation; messages: Message[] }>('GET', `/api/conversations/${id}/messages`),
  deleteConversation: (id: string) => request<{ deleted: string }>('DELETE', `/api/conversations/${id}`),

  chat: (message: string, conversationId?: string | null) =>
    request<TurnResult>('POST', '/api/chat', { message, conversationId }),

  memories: (query?: string, type?: string) => {
    const params = new URLSearchParams();
    if (query) params.set('query', query);
    if (type) params.set('type', type);
    const suffix = params.toString() ? `?${params}` : '';
    // Search results carry a relevance score; plain listing does not.
    return request<{ memories: (Memory & { score?: number })[] }>('GET', `/api/memories${suffix}`);
  },
  createMemory: (input: { type: string; content: string; importance?: number }) =>
    request<{ memory: Memory }>('POST', '/api/memories', input),
  deleteMemory: (id: string) => request<{ deleted: string }>('DELETE', `/api/memories/${id}`),

  tools: () =>
    request<{ tools: ToolInfo[]; approvalRequiredLevels: RiskLevel[] }>('GET', '/api/tools'),
  agents: () => request<{ agents: AgentInfo[]; charterErrors: string[] }>('GET', '/api/agents'),
  runAgent: (name: string, task: string, conversationId?: string | null) =>
    request<{ result: { agent: string; output: string; iterations: number; stoppedBecause: string } }>(
      'POST',
      `/api/agents/${name}/run`,
      { task, conversationId },
    ),

  approvals: () => request<{ approvals: ApprovalRequest[] }>('GET', '/api/approvals'),
  approve: (id: string, note?: string) =>
    request<{ ok: boolean; state: string; message: string; turn?: TurnResult }>(
      'POST',
      `/api/approvals/${id}/approve`,
      { note },
    ),
  deny: (id: string, note?: string) =>
    request<{ ok: boolean; state: string; message: string; turn?: TurnResult }>(
      'POST',
      `/api/approvals/${id}/deny`,
      { note },
    ),

  audit: (limit = 100) => request<{ events: AuditEntry[] }>('GET', `/api/audit?limit=${limit}`),
  events: (limit = 100) => request<{ events: JarvisEvent[] }>('GET', `/api/events?limit=${limit}`),

  voiceStatus: () =>
    request<{
      stt: ProviderStatus & { mode: string };
      tts: ProviderStatus & { mode: string };
    }>('GET', '/api/voice/status'),
  speak: (text: string) =>
    request<{ audioB64: string; mimeType: string }>('POST', '/api/voice/speak', { text }),
  transcribe: (audioB64: string, mimeType: string) =>
    request<{ text: string }>('POST', '/api/voice/transcribe', { audioB64, mimeType }),

  mediaStatus: () =>
    request<{
      image: ProviderStatus;
      imageEdit: ProviderStatus;
      video: ProviderStatus;
      vision: ProviderStatus;
    }>('GET', '/api/media/status'),
};
