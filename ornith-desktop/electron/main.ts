import { app, BrowserWindow, clipboard, dialog, ipcMain, nativeTheme, session, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { DatabaseSync } from 'node:sqlite';

import { DEFAULT_MODEL, STATUS_POLL_INTERVAL_MS } from '../shared/defaults';
import { IPC_VERSION } from '../shared/ipc';
import type { AppInfo, OllamaStatus, Settings, ThinkingMode } from '../shared/types';
import { fetchStatus, probeThinkingMode } from './ollama/client';
import { createConversationStore, type ConversationStore } from './store/conversations';
import { openDatabase } from './store/db';
import { createSettingsStore, type SettingsStore } from './store/settings';
import { createOrchestrator, type Orchestrator } from './chat/orchestrator';
import { createDenyAllBroker, createToolRegistry } from './agent/registry';
import { buildAppMenu } from './menu';
import { initLogger, log } from './log';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;
const isDev = Boolean(DEV_SERVER_URL);

let mainWindow: BrowserWindow | null = null;
let db: DatabaseSync;
let store: ConversationStore;
let settingsStore: SettingsStore;
let orchestrator: Orchestrator;

let status: OllamaStatus | null = null;
let thinkingMode: ThinkingMode = 'none';
let probedModel: string | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

// V1 agent surface: registered but empty, and every request is denied.
const toolRegistry = createToolRegistry();
const permissionBroker = createDenyAllBroker();

/* ------------------------------------------------------------------ window */

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 640,
    minHeight: 480,
    show: false,
    ...(process.platform === 'darwin' ? { titleBarStyle: 'hiddenInset' as const } : {}),
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#101113' : '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
      devTools: isDev || process.env.ORNITH_DEVTOOLS === '1',
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow?.show());

  // External links go to the real browser; nothing opens inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });

  // Block navigation away from the app's own origin.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = DEV_SERVER_URL ? url.startsWith(DEV_SERVER_URL) : url.startsWith('file://');
    if (!allowed) {
      event.preventDefault();
      log.warn('navigation.blocked', { url });
    }
  });

  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    log.error('renderer.gone', { reason: details.reason });
    if (details.reason !== 'clean-exit') mainWindow?.reload();
  });

  if (DEV_SERVER_URL) {
    void mainWindow.loadURL(DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function applyContentSecurityPolicy(): void {
  // Dev needs inline scripts and a websocket for HMR; production does not.
  if (isDev) return;

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            // The renderer never talks to the network; all of it goes over IPC.
            "connect-src 'none'",
          ].join('; '),
        ],
      },
    });
  });
}

/* ------------------------------------------------------------------ status */

function broadcast(channel: string, payload: unknown): void {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) win.webContents.send(channel, payload);
  }
}

function statusChanged(a: OllamaStatus | null, b: OllamaStatus): boolean {
  if (!a) return true;
  return (
    a.connected !== b.connected ||
    a.version !== b.version ||
    a.activeModel !== b.activeModel ||
    a.activeModelInstalled !== b.activeModelInstalled ||
    a.thinkingMode !== b.thinkingMode ||
    a.error?.code !== b.error?.code ||
    a.models.join(',') !== b.models.join(',')
  );
}

async function refreshStatus(): Promise<OllamaStatus> {
  const settings = settingsStore.get();
  const next = await fetchStatus(settings.ollamaUrl, settings.model, DEFAULT_MODEL, thinkingMode);

  // Probe reasoning support once per model, only when it is actually installed.
  if (next.connected && next.activeModelInstalled && probedModel !== next.activeModel) {
    probedModel = next.activeModel;
    thinkingMode = await probeThinkingMode(settings.ollamaUrl, next.activeModel);
    next.thinkingMode = thinkingMode;
    log.info('thinking.probed', { model: next.activeModel, mode: thinkingMode });
  } else {
    next.thinkingMode = thinkingMode;
  }

  if (!next.connected) {
    // Force a re-probe once the server comes back.
    probedModel = null;
  }

  if (statusChanged(status, next)) {
    status = next;
    broadcast('ollama:status-changed', next);
    log.info('ollama.status', {
      connected: next.connected,
      model: next.activeModel,
      installed: next.activeModelInstalled,
    });
  } else {
    status = next;
  }

  return next;
}

/* --------------------------------------------------------------------- ipc */

function registerIpc(): void {
  ipcMain.handle('app:info', (): AppInfo => ({
    version: app.getVersion(),
    platform: process.platform,
    ipcVersion: IPC_VERSION,
  }));

  ipcMain.handle('ollama:status', async () => status ?? (await refreshStatus()));
  ipcMain.handle('ollama:refresh', () => refreshStatus());

  ipcMain.handle('settings:get', () => settingsStore.get());
  ipcMain.handle('settings:update', async (_e, patch: Partial<Settings>) => {
    const before = settingsStore.get();
    const next = settingsStore.update(patch ?? {});

    if (next.ollamaUrl !== before.ollamaUrl || next.model !== before.model) {
      probedModel = null;
      thinkingMode = 'none';
      void refreshStatus();
    }
    if (next.theme !== before.theme) {
      nativeTheme.themeSource = next.theme === 'system' ? 'system' : next.theme;
    }
    return next;
  });

  ipcMain.handle('conv:list', () => store.list());
  ipcMain.handle('conv:get', (_e, id: string) =>
    typeof id === 'string' ? store.get(id) : null,
  );
  ipcMain.handle('conv:create', () => store.create(settingsStore.get().model));
  ipcMain.handle('conv:rename', (_e, req: { id: string; title: string }) => {
    if (typeof req?.id === 'string' && typeof req?.title === 'string') {
      store.rename(req.id, req.title);
    }
  });
  ipcMain.handle('conv:delete', (_e, id: string) => {
    if (typeof id === 'string') store.remove(id);
  });
  ipcMain.handle('conv:clear', (_e, id: string) => {
    if (typeof id === 'string') store.clear(id);
  });

  ipcMain.handle('clipboard:write', (_e, text: string) => {
    if (typeof text === 'string') clipboard.writeText(text);
  });

  ipcMain.on('chat:start', (event, req: { conversationId: string; requestId: string; userText: string }) => {
    // The renderer is treated as untrusted: validate before acting.
    if (
      typeof req?.conversationId !== 'string' ||
      typeof req?.requestId !== 'string' ||
      typeof req?.userText !== 'string' ||
      !req.userText.trim()
    ) {
      log.warn('ipc.invalid', { channel: 'chat:start' });
      return;
    }
    orchestrator.start(req, event.sender);
  });

  ipcMain.on('chat:abort', (_event, req: { requestId: string }) => {
    if (typeof req?.requestId === 'string') orchestrator.abort(req.requestId);
  });
}

/* ---------------------------------------------------------------- lifecycle */

function bootstrap(): void {
  // Test hook: lets E2E point at a scratch profile without touching real data.
  const overrideUserData = process.env.ORNITH_USER_DATA;
  if (overrideUserData) app.setPath('userData', overrideUserData);

  const userData = app.getPath('userData');
  initLogger(path.join(userData, 'logs'));

  try {
    db = openDatabase(path.join(userData, 'ornith.db'));
    store = createConversationStore(db);
  } catch (err) {
    log.error('db.open.failed', { detail: String(err) });
    dialog.showErrorBox(
      'Ornith Desktop',
      `Conversation history couldn't be opened.\n\n${String(err)}\n\nLogs: ${path.join(userData, 'logs')}`,
    );
    app.exit(1);
    return;
  }

  const repaired = store.recoverInterrupted();
  if (repaired > 0) log.warn('startup.recovered', { messages: repaired });

  settingsStore = createSettingsStore(path.join(userData, 'settings.json'), (next) =>
    broadcast('settings:changed', next),
  );

  // Test hook: point the app at a stub Ollama server.
  if (process.env.ORNITH_OLLAMA_URL) {
    settingsStore.update({ ollamaUrl: process.env.ORNITH_OLLAMA_URL });
  }

  const initial = settingsStore.get();
  nativeTheme.themeSource = initial.theme === 'system' ? 'system' : initial.theme;

  orchestrator = createOrchestrator({
    store,
    getSettings: () => settingsStore.get(),
    getThinkingMode: () => thinkingMode,
  });

  log.info('startup', {
    version: app.getVersion(),
    tools: toolRegistry.list().length,
    broker: permissionBroker ? 'deny-all' : 'none',
  });
}

void app.whenReady().then(async () => {
  bootstrap();
  applyContentSecurityPolicy();
  registerIpc();
  buildAppMenu(() => mainWindow);
  createWindow();

  await refreshStatus();
  pollTimer = setInterval(() => void refreshStatus(), STATUS_POLL_INTERVAL_MS);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (pollTimer) clearInterval(pollTimer);
  orchestrator?.abortAll();
  try {
    db?.close();
  } catch {
    /* already closed */
  }
});
