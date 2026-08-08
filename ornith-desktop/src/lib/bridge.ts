import type { IpcEventMap } from '../../shared/ipc';
import type {
  AppInfo,
  Conversation,
  ConversationSummary,
  OllamaStatus,
  Settings,
} from '../../shared/types';

export interface OrnithApi {
  ipcVersion: number;
  appInfo(): Promise<AppInfo>;
  ollama: {
    status(): Promise<OllamaStatus>;
    refresh(): Promise<OllamaStatus>;
  };
  settings: {
    get(): Promise<Settings>;
    update(patch: Partial<Settings>): Promise<Settings>;
  };
  conversations: {
    list(): Promise<ConversationSummary[]>;
    get(id: string): Promise<Conversation | null>;
    create(): Promise<Conversation>;
    rename(id: string, title: string): Promise<void>;
    remove(id: string): Promise<void>;
    clear(id: string): Promise<void>;
  };
  chat: {
    start(payload: { conversationId: string; requestId: string; userText: string }): void;
    abort(requestId: string): void;
  };
  copyText(text: string): Promise<void>;
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
