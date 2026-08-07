import { clipboard, contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

interface WireMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

interface ChatStats {
  evalCount: number;
  evalDurationNs: number;
  totalDurationNs: number;
}

/**
 * The entire surface the renderer gets. No `ipcRenderer`, no Node, no fetch —
 * just these six functions.
 */
const api = {
  getStatus: () => ipcRenderer.invoke('ollama:status'),

  // Electron's clipboard rather than navigator.clipboard: the packaged app is
  // loaded over file://, which is not a secure context for the web API.
  copyText: (text: string) => clipboard.writeText(text),

  sendChat: (request: { requestId: string; model: string; messages: WireMessage[] }) =>
    ipcRenderer.send('ollama:chat', request),

  abortChat: (requestId: string) => ipcRenderer.send('ollama:abort', requestId),

  onDelta: (callback: (payload: { requestId: string; text: string }) => void) =>
    subscribe('ollama:delta', callback),

  onDone: (callback: (payload: { requestId: string; stats: ChatStats }) => void) =>
    subscribe('ollama:done', callback),

  onError: (callback: (payload: { requestId: string; message: string }) => void) =>
    subscribe('ollama:error', callback),
};

/** Wraps an ipcRenderer.on and hands back an unsubscribe, so effects can clean up. */
function subscribe<T>(channel: string, callback: (payload: T) => void): () => void {
  const listener = (_event: IpcRendererEvent, payload: T) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

contextBridge.exposeInMainWorld('ornith', api);

export type OrnithApi = typeof api;
