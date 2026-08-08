import type {
  SpeakRequest,
  TranscriptionRequest,
  TranscriptionResult,
  TtsState,
  VoiceCapabilities,
} from './voice';
import type {
  AppInfo,
  ChatMessage,
  Conversation,
  ConversationSummary,
  OllamaStatus,
  Settings,
  StreamOutcome,
  StreamState,
} from './types';

export const IPC_VERSION = 1;

/** Request/response channels, served by ipcMain.handle. */
export interface IpcInvokeMap {
  'app:info': { req: void; res: AppInfo };
  'ollama:status': { req: void; res: OllamaStatus };
  'ollama:refresh': { req: void; res: OllamaStatus };
  'settings:get': { req: void; res: Settings };
  'settings:update': { req: Partial<Settings>; res: Settings };
  'conv:list': { req: void; res: ConversationSummary[] };
  'conv:get': { req: string; res: Conversation | null };
  'conv:create': { req: { title?: string } | undefined; res: Conversation };
  'conv:rename': { req: { id: string; title: string }; res: void };
  /** Native confirm before a destructive delete. Resolves true when empty (no prompt). */
  'conv:confirm-delete': { req: { title: string; messageCount: number }; res: boolean };
  'conv:delete': { req: string; res: void };
  'conv:clear': { req: string; res: void };
  'clipboard:write': { req: string; res: void };

  /* ---- voice layer ---- */
  'voice:capabilities': { req: void; res: VoiceCapabilities };
  /** WAV bytes in, transcript out. Audio never leaves the machine. */
  'voice:transcribe': { req: TranscriptionRequest; res: TranscriptionResult };
  'tts:speak': { req: SpeakRequest; res: void };
  'tts:stop': { req: void; res: void };
}

/** Fire-and-forget renderer → main channels. */
export interface IpcSendMap {
  'chat:start': { conversationId: string; requestId: string; userText: string };
  'chat:abort': { requestId: string };
}

/** Main → renderer events. */
export interface IpcEventMap {
  /** Emitted once the turn is persisted, carrying the store-assigned message ids. */
  'chat:started': {
    requestId: string;
    conversationId: string;
    userMessage: ChatMessage;
    assistantMessage: ChatMessage;
    title: string;
  };
  'chat:state': { requestId: string; state: StreamState };
  'chat:delta': { requestId: string; content: string; thinking: string };
  /** `thinkingIncomplete` marks an unclosed <think> block; the turn itself may still be complete. */
  'chat:end': { requestId: string; outcome: StreamOutcome; thinkingIncomplete?: boolean };
  'chat:trimmed': { requestId: string; droppedMessages: number };
  'ollama:status-changed': OllamaStatus;
  'settings:changed': Settings;
  'tts:state': TtsState;

  /** Menu accelerators, forwarded to the renderer as commands. */
  'menu:new-chat': Record<string, never>;
  'menu:delete-chat': Record<string, never>;
  'menu:open-settings': Record<string, never>;
  'menu:focus-composer': Record<string, never>;
  'menu:stop-generating': Record<string, never>;
  'menu:prev-chat': Record<string, never>;
  'menu:next-chat': Record<string, never>;
}

export type IpcInvokeChannel = keyof IpcInvokeMap;
export type IpcSendChannel = keyof IpcSendMap;
export type IpcEventChannel = keyof IpcEventMap;

export const INVOKE_CHANNELS = [
  'app:info',
  'ollama:status',
  'ollama:refresh',
  'settings:get',
  'settings:update',
  'conv:list',
  'conv:get',
  'conv:create',
  'conv:rename',
  'conv:confirm-delete',
  'conv:delete',
  'voice:capabilities',
  'voice:transcribe',
  'tts:speak',
  'tts:stop',
  'conv:clear',
  'clipboard:write',
] as const satisfies readonly IpcInvokeChannel[];

export const EVENT_CHANNELS = [
  'chat:started',
  'chat:state',
  'chat:delta',
  'chat:end',
  'chat:trimmed',
  'ollama:status-changed',
  'settings:changed',
  'tts:state',
  'menu:new-chat',
  'menu:delete-chat',
  'menu:open-settings',
  'menu:focus-composer',
  'menu:stop-generating',
  'menu:prev-chat',
  'menu:next-chat',
] as const satisfies readonly IpcEventChannel[];
