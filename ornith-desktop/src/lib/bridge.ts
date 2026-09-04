import type { IpcEventMap } from '../../shared/ipc';
import type {
  AppInfo,
  Conversation,
  ConversationSummary,
  ExportRequest,
  ExportResult,
  SearchRequest,
  SearchResult,
  OllamaStatus,
  PublicSettings,
  Settings,
} from '../../shared/types';
import type {
  SpeakRequest,
  TranscriptionResult,
  VoiceCapabilities,
} from '../../shared/voice';

export interface OrnithApi {
  ipcVersion: number;
  appInfo(): Promise<AppInfo>;
  ollama: {
    status(): Promise<OllamaStatus>;
    refresh(): Promise<OllamaStatus>;
  };
  settings: {
    get(): Promise<PublicSettings>;
    update(patch: Partial<Settings>): Promise<PublicSettings>;
  };
  conversations: {
    list(): Promise<ConversationSummary[]>;
    get(id: string): Promise<Conversation | null>;
    create(): Promise<Conversation>;
    rename(id: string, title: string): Promise<void>;
    confirmDelete(title: string, messageCount: number): Promise<boolean>;
    remove(id: string): Promise<void>;
    clear(id: string): Promise<void>;
    export(request: ExportRequest): Promise<ExportResult>;
    search(request: SearchRequest): Promise<SearchResult>;
  };
  chat: {
    start(payload: { conversationId: string; requestId: string; userText: string }): void;
    abort(requestId: string): void;
  };
  copyText(text: string): Promise<void>;
  voice: {
    capabilities(): Promise<VoiceCapabilities>;
    transcribe(wav: Uint8Array, locale: string): Promise<TranscriptionResult>;
    speak(request: SpeakRequest): Promise<void>;
    stopSpeaking(): Promise<void>;
  };
  on<K extends keyof IpcEventMap>(
    channel: K,
    callback: (payload: IpcEventMap[K]) => void,
  ): () => void;
}

declare global {
  interface Window {
    ornith: OrnithApi;
  }
}

export const bridge: OrnithApi = window.ornith;

export function newRequestId(): string {
  return crypto.randomUUID();
}
