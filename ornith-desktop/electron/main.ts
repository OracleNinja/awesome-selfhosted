import { app, BrowserWindow, ipcMain, session, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { getStatus, streamChat, type WireMessage } from './ollama.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;
const isDev = Boolean(DEV_SERVER_URL);

/** Active streams, so Stop can cancel the right one. */
const inFlight = new Map<string, () => void>();

let mainWindow: BrowserWindow | null = null;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 640,
    minHeight: 480,
    show: false,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  // Avoid a white flash before React paints.
  mainWindow.once('ready-to-show', () => mainWindow?.show());

  // External links open in the real browser, never in an app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
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
  // Only in production: the dev server needs inline/eval and a websocket for HMR.
  // The renderer makes no network requests of its own either way — all Ollama
  // traffic goes through IPC to the main process.
  if (isDev) return;

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'",
        ],
      },
    });
  });
}

function registerIpc(): void {
  ipcMain.handle('ollama:status', () => getStatus());

  ipcMain.on(
    'ollama:chat',
    (
      event,
      request: { requestId: string; model: string; messages: WireMessage[] },
    ) => {
      const { requestId, model, messages } = request;
      const sender = event.sender;

      // Guard against a duplicate id leaving an orphaned stream running.
      inFlight.get(requestId)?.();

      const abort = streamChat(
        { model, messages },
        {
          onDelta: (text) => {
            if (!sender.isDestroyed()) sender.send('ollama:delta', { requestId, text });
          },
          onDone: (stats) => {
            inFlight.delete(requestId);
            if (!sender.isDestroyed()) sender.send('ollama:done', { requestId, stats });
          },
          onError: (message) => {
            inFlight.delete(requestId);
            if (!sender.isDestroyed()) sender.send('ollama:error', { requestId, message });
          },
        },
      );

      inFlight.set(requestId, abort);
    },
  );

  ipcMain.on('ollama:abort', (_event, requestId: string) => {
    inFlight.get(requestId)?.();
    inFlight.delete(requestId);
  });
}

void app.whenReady().then(() => {
  applyContentSecurityPolicy();
  registerIpc();
  createWindow();

  // macOS: clicking the dock icon with no windows open reopens one.
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  // Standard macOS behaviour is to stay resident; quit elsewhere.
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  for (const abort of inFlight.values()) abort();
  inFlight.clear();
});
