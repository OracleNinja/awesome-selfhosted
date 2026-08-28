/**
 * The entire surface the renderer gets. Runs sandboxed, so `electron` is the
 * only module it may require — everything privileged goes over IPC.
 */
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import { IPC_VERSION } from '../shared/ipc';
import type { IpcEventMap, IpcInvokeMap, IpcSendMap } from '../shared/ipc';

function invoke<K extends keyof IpcInvokeMap>(
  channel: K,
  payload?: IpcInvokeMap[K]['req'],
): Promise<IpcInvokeMap[K]['res']> {
  return ipcRenderer.invoke(channel, payload) as Promise<IpcInvokeMap[K]['res']>;
}

function send<K extends keyof IpcSendMap>(channel: K, payload: IpcSendMap[K]): void {
  ipcRenderer.send(channel, payload);
}

/** Returns an unsubscribe so React effects can clean up properly. */
function on<K extends keyof IpcEventMap>(
  channel: K,
  callback: (payload: IpcEventMap[K]) => void,
): () => void {
  const listener = (_event: IpcRendererEvent, payload: IpcEventMap[K]) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

const api = {
  ipcVersion: IPC_VERSION,

  appInfo: () => invoke('app:info'),

  ollama: {
    status: () => invoke('ollama:status'),
    refresh: () => invoke('ollama:refresh'),
  },

  settings: {
    get: () => invoke('settings:get'),
    update: (patch: IpcInvokeMap['settings:update']['req']) => invoke('settings:update', patch),
  },

  conversations: {
    list: () => invoke('conv:list'),
    get: (id: string) => invoke('conv:get', id),
    create: () => invoke('conv:create', undefined),
    rename: (id: string, title: string) => invoke('conv:rename', { id, title }),
    confirmDelete: (title: string, messageCount: number) =>
      invoke('conv:confirm-delete', { title, messageCount }),
    remove: (id: string) => invoke('conv:delete', id),
    clear: (id: string) => invoke('conv:clear', id),
  },

  chat: {
    start: (payload: IpcSendMap['chat:start']) => send('chat:start', payload),
    abort: (requestId: string) => send('chat:abort', { requestId }),
  },

  // Electron's clipboard rather than navigator.clipboard: a packaged app loads
  // over file://, which is not a secure context for the web API.
  copyText: (text: string) => invoke('clipboard:write', text),

  portable: {
    info: () => invoke('portable:info'),
  },

  voice: {
    capabilities: () => invoke('voice:capabilities'),
    /** WAV bytes go to main over IPC; they never touch the network. */
    transcribe: (wav: Uint8Array, locale: string) => invoke('voice:transcribe', { wav, locale }),
    speak: (request: IpcInvokeMap['tts:speak']['req']) => invoke('tts:speak', request),
    stopSpeaking: () => invoke('tts:stop'),
    /** Renderer playback only: tells main the audio it sent has finished. */
    finishedSpeaking: (requestId: string) => send('tts:finished', { requestId }),
  },

  on,
};

contextBridge.exposeInMainWorld('ornith', api);

export type OrnithApi = typeof api;
