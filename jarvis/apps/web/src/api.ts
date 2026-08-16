/**
 * Browser API client.
 *
 * The browser holds exactly one credential: the JARVIS API token the user
 * pasted into Settings (stored in localStorage). It never sees a provider key —
 * those stay on the server, and the server has no route that returns one.
 */

export type RiskLevel = 'READ' | 'WRITE' | 'EXTERNAL_ACTION' | 'DESTRUCTIVE';

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface Message {
  id: string;
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  agent: string | null;
  createdAt: string;
  toolCalls?: { id: string; name: string; arguments: Record<string, unknown> }[];
  toolCallId?: string;
  name?: string;
}

export interface JarvisEvent {
  id: string;
  type: string;
  conversationId: string | null;
  agent: string;
  summary: string;
  data: Record<string, unknown>;
  createdAt: string;
}

export interface Approval {
  id: string;
  agent: string;
  tool: string;
  description: string;
  risk: RiskLevel;
  arguments: Record<string, unknown>;
  state: string;
  createdAt: string;
  expiresAt: string;
}

export interface Memory {
  id: string;
  type: string;
  content: string;
  source: string;
  confidence: number;
  importance: number;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
  score?: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  risk: RiskLevel;
  requiresApproval: boolean;
  available: boolean;
  unavailableReason?: string;
  inputSchema: { properties?: Record<string, { type?: string; description?: string }>; required?: string[] };
}

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

export interface ProviderStatus {
  id: string;
  kind: string;
  available: boolean;
  reason?: string;
  model?: string;
  endpoint?: string;
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
  pendingApprovals: Approval[];
  events: JarvisEvent[];
  model: string;
  provider: string;
  iterations: number;
  error?: string;
}

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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
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
    const error = payload?.error ?? {};
    throw new ApiError(response.status, error.code ?? 'error', error.message ?? response.statusText);
  }
  return payload as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>('GET', '/api/health'),
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
    return request<{ memories: Memory[] }>('GET', `/api/memories${suffix}`);
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

  approvals: () => request<{ approvals: Approval[] }>('GET', '/api/approvals'),
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

/**
 * Live event stream.
 *
 * EventSource cannot set an Authorization header, so in token mode the token is
 * passed as a query parameter on this one same-origin endpoint. It is the
 * user's own session token, not a provider credential.
 */
export function subscribeToEvents(onEvent: (event: JarvisEvent) => void): () => void {
  const token = getToken();
  const url = token ? `/api/events/stream?token=${encodeURIComponent(token)}` : '/api/events/stream';
  let source: EventSource | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    if (closed) return;
    source = new EventSource(url);
    source.addEventListener('jarvis', (message) => {
      try {
        onEvent(JSON.parse((message as MessageEvent).data) as JarvisEvent);
      } catch {
        /* ignore malformed frame */
      }
    });
    source.onerror = () => {
      source?.close();
      if (!closed) retry = setTimeout(connect, 3000);
    };
  };

  connect();
  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    source?.close();
  };
}
