import { app, Menu, shell, type BrowserWindow } from 'electron';

/**
 * A full native menu. The Edit menu's standard roles are not optional: without
 * them, Cmd+C/V/X and Select All stop working entirely in the renderer, which
 * is a classic Electron omission.
 */
export function buildAppMenu(getWindow: () => BrowserWindow | null): void {
  const isMac = process.platform === 'darwin';

  const send = (channel: string) => () => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, {});
  };

  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac
      ? ([
          {
            label: app.name,
            submenu: [
              { role: 'about' },
              { type: 'separator' },
              {
                label: 'Settings…',
                accelerator: 'CmdOrCtrl+,',
                click: send('menu:open-settings'),
              },
              { type: 'separator' },
              { role: 'services' },
              { type: 'separator' },
              { role: 'hide' },
              { role: 'hideOthers' },
              { role: 'unhide' },
              { type: 'separator' },
              { role: 'quit' },
            ],
          },
        ] as Electron.MenuItemConstructorOptions[])
      : []),

    {
      label: 'File',
      submenu: [
        { label: 'New Chat', accelerator: 'CmdOrCtrl+N', click: send('menu:new-chat') },
        {
          label: 'Delete Chat',
          accelerator: 'CmdOrCtrl+Shift+Backspace',
          click: send('menu:delete-chat'),
        },
        { type: 'separator' },
        ...(isMac
          ? ([{ role: 'close' }] as Electron.MenuItemConstructorOptions[])
          : ([
              {
                label: 'Settings…',
                accelerator: 'CmdOrCtrl+,',
                click: send('menu:open-settings'),
              },
              { type: 'separator' },
              { role: 'quit' },
            ] as Electron.MenuItemConstructorOptions[])),
      ],
    },

    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
        { type: 'separator' },
        {
          label: 'Focus Composer',
          accelerator: 'CmdOrCtrl+K',
          click: send('menu:focus-composer'),
        },
      ],
    },

    {
      label: 'Chat',
      submenu: [
        {
          // Cmd+. is the macOS convention for cancel. Escape is deliberately
          // NOT used here: a menu accelerator is captured before the renderer
          // sees the key, which would break Escape for closing the settings
          // dialog and cancelling an inline rename. The renderer handles
          // Escape itself, where it can tell those contexts apart.
          label: 'Stop Generating',
          accelerator: 'CmdOrCtrl+.',
          click: send('menu:stop-generating'),
        },
        { type: 'separator' },
        {
          label: 'Previous Chat',
          accelerator: 'CmdOrCtrl+[',
          click: send('menu:prev-chat'),
        },
        {
          label: 'Next Chat',
          accelerator: 'CmdOrCtrl+]',
          click: send('menu:next-chat'),
        },
      ],
    },

    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },

    {
      label: 'Window',
      submenu: isMac
        ? [{ role: 'minimize' }, { role: 'zoom' }, { type: 'separator' }, { role: 'front' }]
        : [{ role: 'minimize' }, { role: 'close' }],
    },

    {
      role: 'help',
      submenu: [
        {
          label: 'Ollama Documentation',
          click: () => void shell.openExternal('https://github.com/ollama/ollama'),
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
