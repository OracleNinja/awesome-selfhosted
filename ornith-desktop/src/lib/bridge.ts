import type { ChatStats, StatusResult } from '../types';

interface WireMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface OrnithBridge {
  getStatus: () => Promise<StatusResult>;
  copyText: (text: string) => void;
  sendChat: (request: { requestId: string; model: string; messages: WireMessage[] }) => void;
  abortChat: (requestId: string) => void;
  onDelta: (cb: (payload: { requestId: string; text: string }) => void) => () => void;
  onDone: (cb: (payload: { requestId: string; stats: ChatStats }) => void) => () => void;
  onError: (cb: (payload: { requestId: string; message: string }) => void) => () => void;
}

declare global {
  interface Window {
    ornith: OrnithBridge;
  }
}

export const bridge: OrnithBridge = window.ornith;
