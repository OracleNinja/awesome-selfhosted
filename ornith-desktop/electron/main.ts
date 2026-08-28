import { app, BrowserWindow, clipboard, dialog, ipcMain, nativeTheme, session, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { DatabaseSync } from 'node:sqlite';

import { DEFAULT_MODEL, DEFAULT_OLLAMA_URL, STATUS_POLL_INTERVAL_MS } from '../shared/defaults';
import { IPC_VERSION } from '../shared/ipc';
import type { AppInfo, OllamaStatus, Settings, ThinkingMode } from '../shared/types';
import { toPublicSettings } from '../shared/types';
import { createOllamaBackend } from './backends/ollama';
import { createGatewayBackend } from './backends/gateway';
import type { ChatBackend } from './backends/types';
import type {
  SpeakRequest,
  TranscriptionRequest,
  TranscriptionResult,
  VoiceCapabilities,
} from '../shared/voice';
import { createTtsEngine, type TtsEngine } from './voice/tts';
import { createSttEngine, type SttEngine } from './voice/stt';
import { fetchStatus, probeThinkingMode } from './ollama/client';
import { createConversationStore, type ConversationStore } from './store/conversations';
import { openDatabase } from './store/db';
import { createSettingsStore, type SettingsStore } from './store/settings';
import { createOrchestrator, type Orchestrator } from './chat/orchestrator';
import { createDenyAllBroker, createToolRegistry } from './agent/registry';
import { buildAppMenu } from './menu';
import { initLogger, log } from './log';
import { detectPortable, ensurePortableDirs, type PortableContext } from './portable/detect';
import { probeWritable, readVolumeStats } from './portable/volume';
import { DEFAULT_MANIFEST, classifyCapacity, type PortableInfo } from '../shared/portable';
import {
  createRuntimeSupervisor,
  type RuntimeState,
  type RuntimeSupervisor,
} from './runtime/supervisor';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;
const isDev = Boolean(DEV_SERVER_URL);

let mainWindow: BrowserWindow | null = null;
let db: DatabaseSync;
let store: ConversationStore;
let settingsStore: SettingsStore;
let orchestrator: Orchestrator;
let ttsEngine: TtsEngine;
let sttEngine: SttEngine;

let status: OllamaStatus | null = null;
let thinkingMode: ThinkingMode = 'none';
let probedModel: string | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

let portable: PortableContext = { portable: false, root: null, manifest: DEFAULT_MANIFEST, layout: null };
let supervisor: RuntimeSupervisor | null = null;
/**
 * The utterance speech is currently for, or null when nothing should be
 * heard. Renderer-played synthesis takes seconds, and both a Stop and a
 * newer reply can land inside that window; without this, audio produced for
 * an abandoned request still reached the speakers.
 */
let speakingFor: string | null = null;
let runtime: RuntimeState = { source: 'unavailable', host: DEFAULT_OLLAMA_URL };

/**
 * The host that actually answers this session. A supervised runtime picks its
 * own port — the configured one may have been taken — and that choice wins
 * without being written back to settings, so an ephemeral port never becomes
 * permanent configuration on the drive.
 */
function ollamaHost(): string {
  if (runtime.source === 'bundled') return runtime.host;
  return settingsStore.get().ollamaUrl;
}

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

function applyPermissionPolicy(): void {
  // Deny-by-default. Only the microphone is allowed, and only audio — the
  // renderer has no legitimate use for camera, geolocation, notifications or
  // anything else. macOS still shows its own TCC prompt on first use.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback, details) => {
    if (permission === 'media') {
      const wanted = (details as { mediaTypes?: string[] }).mediaTypes ?? [];
      const audioOnly = wanted.length === 0 || wanted.every((type) => type === 'audio');
      if (!audioOnly) log.warn('permission.denied', { permission, wanted });
      return callback(audioOnly);
    }
    log.warn('permission.denied', { permission });
    callback(false);
  });

  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === 'media');
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
  const host = ollamaHost();
  const next = await fetchStatus(host, settings.model, DEFAULT_MODEL, thinkingMode);

  // Probe reasoning support once per model, only when it is actually installed.
  if (next.connected && next.activeModelInstalled && probedModel !== next.activeModel) {
    probedModel = next.activeModel;
    thinkingMode = await probeThinkingMode(host, next.activeModel);
    next.thinkingMode = thinkingMode;
    log.info('thinking.probed', { model: next.activeModel, mode: thinkingMode });
  } else {
    next.thinkingMode = thinkingMode;
  }

  if (!next.connected) {
    // Force a re-probe once the server comes back.
    probedModel = null;

    // On a drive, "is Ollama running?" is the wrong question — the drive was
    // supposed to start it. The supervisor knows why it could not, and names
    // the file that is missing, so its reason replaces the generic one.
    if (runtime.source === 'unavailable' && runtime.reason && next.error) {
      next.error = { ...next.error, message: runtime.reason };
    }
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

/* ---------------------------------------------------------------- portable */

/**
 * Started only in portable mode. An installed Ornith keeps its existing
 * contract with the machine's own Ollama: it connects to one, it never starts
 * one. Portable mode is the case where there may be nothing to connect to.
 */
async function startRuntime(): Promise<void> {
  if (!portable.portable || !portable.layout) return;

  supervisor = createRuntimeSupervisor({
    layout: portable.layout,
    configuredHost: settingsStore.get().ollamaUrl,
    platform: process.platform,
    arch: process.arch,
    onLog: (event, fields) => log.info(event, fields),
  });

  runtime = await supervisor.start();

  if (runtime.source === 'unavailable') {
    log.warn('runtime.unavailable', { reason: runtime.reason });
  }

  // Pushed rather than polled: a cold start can outlast the renderer's first
  // read, and the status poll only broadcasts when the status itself changes.
  broadcast('portable:changed', portableInfo());
}

/** Volume stats are probed per call: free space moves while the app runs. */
function portableInfo(): PortableInfo {
  const layout = portable.layout;
  const volume = layout ? readVolumeStats(layout.dataDir) : null;

  return {
    portable: portable.portable,
    root: portable.root,
    label: portable.manifest.label,
    dataDir: layout?.dataDir ?? app.getPath('userData'),
    modelsDir: layout?.modelsDir ?? '',
    runtime: { ...runtime, host: ollamaHost() },
    volume,
    capacity: classifyCapacity(volume),
  };
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

  // PublicSettings, never Settings: the gateway token stays in main.
  ipcMain.handle('settings:get', () => toPublicSettings(settingsStore.get()));
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
    return toPublicSettings(next);
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
  ipcMain.handle(
    'conv:confirm-delete',
    async (_e, req: { title: string; messageCount: number }): Promise<boolean> => {
      // An empty chat has nothing to lose, so don't nag about it.
      if (!req || typeof req.messageCount !== 'number' || req.messageCount === 0) return true;

      const win = BrowserWindow.getFocusedWindow() ?? mainWindow;
      if (!win) return false;

      const { response } = await dialog.showMessageBox(win, {
        type: 'warning',
        buttons: ['Cancel', 'Delete'],
        defaultId: 0,
        cancelId: 0,
        message: `Delete “${req.title}”?`,
        detail: 'This conversation and all of its messages will be permanently removed.',
      });
      return response === 1;
    },
  );

  ipcMain.handle('conv:delete', (_e, id: string) => {
    if (typeof id === 'string') store.remove(id);
  });
  ipcMain.handle('conv:clear', (_e, id: string) => {
    if (typeof id === 'string') store.clear(id);
  });

  ipcMain.handle('clipboard:write', (_e, text: string) => {
    if (typeof text === 'string') clipboard.writeText(text);
  });

  ipcMain.handle('portable:info', (): PortableInfo => portableInfo());

  /* ---- voice layer ---------------------------------------------------- */

  ipcMain.handle('voice:capabilities', async (): Promise<VoiceCapabilities> => {
    const tts = ttsEngine.availability();
    return {
      stt: sttEngine.availability(),
      tts: { ...tts, voices: tts.available ? await ttsEngine.listVoices() : [] },
    };
  });

  ipcMain.handle(
    'voice:transcribe',
    async (_e, req: TranscriptionRequest): Promise<TranscriptionResult> => {
      if (!req || !(req.wav instanceof Uint8Array) || req.wav.byteLength === 0) {
        return { text: '', error: 'No audio was captured.' };
      }
      const locale = typeof req.locale === 'string' ? req.locale : settingsStore.get().sttLocale;
      return sttEngine.transcribe({ wav: req.wav, locale });
    },
  );

  ipcMain.handle('tts:speak', async (_e, req: SpeakRequest) => {
    if (!req || typeof req.text !== 'string' || typeof req.requestId !== 'string') return;

    const settings = settingsStore.get();
    const request: SpeakRequest = {
      requestId: req.requestId,
      text: req.text,
      voice: typeof req.voice === 'string' ? req.voice : settings.voiceName,
      rate: Number.isFinite(req.rate) ? req.rate : settings.speechRate,
    };

    speakingFor = request.requestId;
    broadcast('tts:state', { speaking: true, requestId: request.requestId });

    if (ttsEngine.availability().playback === 'native') {
      ttsEngine.speak(request, (finishedId) => {
        if (speakingFor === finishedId) speakingFor = null;
        broadcast('tts:state', { speaking: false, requestId: finishedId });
      });
      return;
    }

    // The drive's engine writes a file; the renderer is what has speakers.
    // Synthesis can take seconds, so the indicator is already on by now.
    const result = await ttsEngine.synthesize(request);

    // Stopped, or superseded by a newer reply, while we were synthesising.
    // Saying it anyway is worse than saying nothing.
    if (speakingFor !== request.requestId) {
      log.info('tts.discarded', { requestId: request.requestId });
      return;
    }

    if (!result.wav) {
      log.warn('tts.synthesis.failed', { detail: result.error });
      speakingFor = null;
      broadcast('tts:state', { speaking: false, requestId: request.requestId });
      return;
    }

    broadcast('tts:audio', { requestId: request.requestId, wav: result.wav });
  });

  // Renderer playback has finished or was stopped: main owns the indicator for
  // both engines, so it has to be told when the half it cannot see is done.
  ipcMain.on('tts:finished', (_e, req: { requestId: string }) => {
    if (typeof req?.requestId !== 'string') return;
    if (speakingFor === req.requestId) speakingFor = null;
    broadcast('tts:state', { speaking: false, requestId: req.requestId });
  });

  ipcMain.handle('tts:stop', () => {
    const id = speakingFor ?? ttsEngine.speakingRequestId;
    // Cleared before stopping, so a synthesis still in flight sees that it has
    // been abandoned and drops its audio instead of broadcasting it.
    speakingFor = null;
    ttsEngine.stop();
    // Renderer playback stops on this too: the player listens for its own id
    // going quiet, so one message covers both engines.
    broadcast('tts:state', { speaking: false, requestId: id });
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

/**
 * Picks the backend for the next turn. Online falls back to local when the
 * gateway has not been configured, so selecting Online without a token can
 * never strand the user with a non-working app.
 */
function resolveBackend(): ChatBackend {
  const settings = settingsStore.get();

  if (settings.mode === 'online' && settings.gatewayUrl && settings.gatewayToken) {
    return createGatewayBackend({
      url: settings.gatewayUrl,
      token: settings.gatewayToken,
    });
  }

  if (settings.mode === 'online') {
    log.warn('backend.online_unconfigured', { hasUrl: Boolean(settings.gatewayUrl) });
  }

  return createOllamaBackend(ollamaHost());
}

/* ---------------------------------------------------------------- lifecycle */

/**
 * Decides where this launch keeps its data, and returns that directory.
 *
 * Must run before anything opens the database or the settings file, because
 * the whole point of portable mode is that those land on the drive rather than
 * in the host's home directory.
 */
function resolveDataRoot(): string {
  // Test hook first: E2E points at a scratch profile, and it must win over a
  // marker that happens to sit above the checkout.
  const overrideUserData = process.env.ORNITH_USER_DATA;
  if (overrideUserData) {
    app.setPath('userData', overrideUserData);
    return app.getPath('userData');
  }

  try {
    portable = detectPortable({ startDir: path.dirname(app.getPath('exe')) });
  } catch (err) {
    // Detection failing must never stop the app starting; it just means this
    // launch is an ordinary installed one. The reason is logged below.
    portable = { ...portable, reason: `Portable detection failed: ${String(err)}` };
  }

  if (!portable.portable || !portable.layout) return app.getPath('userData');

  // A write-protected or read-only drive is an ordinary state for removable
  // media. Say so plainly here rather than failing later inside SQLite, where
  // the message would mean nothing. `mkdir -p` succeeds on an existing tree
  // even when the mount is read-only, so the probe write is the real test.
  let failure: string | null = null;
  try {
    ensurePortableDirs(portable.layout);
    if (!probeWritable(portable.layout.dataDir)) failure = 'The drive is read-only.';
  } catch (err) {
    failure = String(err);
  }

  if (failure) {
    dialog.showErrorBox(
      'Ornith Portable',
      `The drive at ${portable.root} can't be written to.\n\n` +
        `${failure}\n\n` +
        'Check that it is not write-protected or mounted read-only, then launch Ornith again.',
    );
    app.exit(1);
    return app.getPath('userData');
  }

  app.setPath('userData', portable.layout.dataDir);
  return portable.layout.dataDir;
}

function bootstrap(): void {
  const userData = resolveDataRoot();
  initLogger(path.join(userData, 'logs'));

  if (portable.portable && portable.layout) {
    // Set before the window exists, so the first thing the renderer reads is
    // "starting" rather than the "unavailable" that has not been tried yet.
    runtime = { source: 'starting', host: DEFAULT_OLLAMA_URL };
    log.info('portable.active', {
      root: portable.root,
      label: portable.manifest.label,
      models: portable.layout.modelsDir,
    });
  } else if (portable.reason) {
    // Logged here rather than at the decision point, which runs before the log
    // file exists — and this is exactly the line someone reads to find out why
    // their drive was not picked up.
    log.warn('portable.declined', { reason: portable.reason });
  }

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
    broadcast('settings:changed', toPublicSettings(next)),
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
    getBackend: resolveBackend,
  });

  ttsEngine = createTtsEngine({ layout: portable.layout });
  sttEngine = createSttEngine({
    appRoot: path.join(__dirname, '..'),
    resourcesPath: process.resourcesPath ?? path.join(__dirname, '..'),
    // The drive is where a cross-platform speech engine can live, so the
    // layout is what lets dictation work off a Mac at all.
    layout: portable.layout,
  });

  log.info('startup', {
    version: app.getVersion(),
    tools: toolRegistry.list().length,
    stt: sttEngine.availability().engine,
    sttAvailable: sttEngine.availability().available,
    ttsAvailable: ttsEngine.availability().available,
    broker: permissionBroker ? 'deny-all' : 'none',
  });
}

void app.whenReady().then(async () => {
  bootstrap();
  applyContentSecurityPolicy();
  applyPermissionPolicy();
  registerIpc();
  buildAppMenu(() => mainWindow);
  createWindow();

  // After the window exists: a cold start off a USB drive can take tens of
  // seconds, and the user should be looking at the app while it happens.
  await startRuntime();

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
  ttsEngine?.stop();
  // stop() sends SIGTERM synchronously; only the SIGKILL fallback is deferred.
  // A server left holding the drive open would block the user from ejecting it.
  void supervisor?.stop();
  try {
    db?.close();
  } catch {
    /* already closed */
  }
});
