import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { startStubOllama, type StubHandle } from '../integration/stubOllama';
import {
  PORTABLE_MARKER,
  resolveLayout,
  runtimeBinaryPath,
  type PortableInfo,
} from '../../shared/portable';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/**
 * The bridge as this suite uses it. Declared locally rather than through a
 * global `Window` augmentation, because the other E2E spec augments the same
 * interface with a different shape.
 */
type OrnithWindow = Window & { ornith: { portable: { info(): Promise<PortableInfo> } } };

const readPortableInfo = (page: Page): Promise<PortableInfo> =>
  page.evaluate(() => (window as unknown as OrnithWindow).ornith.portable.info());

let stub: StubHandle;
let app: ElectronApplication;
let page: Page;
/** Stands in for the USB volume; a plain directory is the same thing to the app. */
let drive: string;
let root: string;

function launchEnv(extra: Record<string, string>): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  // Never inherited: the whole point of a portable launch is that the app works
  // out where its data goes without being told.
  delete env.ORNITH_USER_DATA;
  delete env.ORNITH_PORTABLE_ROOT;

  return { ...env, ORNITH_OLLAMA_URL: stub.url, NODE_ENV: 'test', ...extra };
}

async function open(env: Record<string, string>): Promise<void> {
  app = await electron.launch({ args: [appRoot, '--no-sandbox', '--disable-gpu'], env });
  page = await app.firstWindow();
  await page.waitForSelector('.app', { timeout: 30_000 });

  const chooser = page.getByTestId('mode-chooser');
  if (await chooser.isVisible().catch(() => false)) {
    await page.getByTestId('choose-local').click();
    await expect(chooser).toHaveCount(0);
  }
}

/** Launches with no ORNITH_USER_DATA: the drive alone decides where data goes. */
const launchPortable = () => open(launchEnv({ ORNITH_PORTABLE_ROOT: root }));

/** A port nothing is listening on, so the external probe is bound to miss. */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as net.AddressInfo;
      server.close(() => resolve(port));
    });
  });
}

function answers(url: string): Promise<boolean> {
  return fetch(`${url}/api/version`)
    .then((response) => response.ok)
    .catch(() => false);
}

/**
 * Installs a stand-in for the Ollama binary a real drive would carry. A shim
 * rather than a copy, so the helper keeps its relative import of the stub.
 */
function installBundledRuntime(): void {
  const layout = resolveLayout(root);
  const binary = runtimeBinaryPath(layout, process.platform, process.arch);
  const helper = path.join(appRoot, 'tests', 'e2e', 'fake-ollama.mjs');

  mkdirSync(path.dirname(binary), { recursive: true });
  writeFileSync(binary, `#!/bin/sh\nexec node ${JSON.stringify(helper)} "$@"\n`);
  chmodSync(binary, 0o755);
}

test.beforeEach(async () => {
  drive = mkdtempSync(path.join(tmpdir(), 'ornith-drive-'));
  root = path.join(drive, 'Ornith');
  stub = await startStubOllama();
});

test.afterEach(async () => {
  await app?.close().catch(() => {});
  await stub?.close();
  rmSync(drive, { recursive: true, force: true });
});

test('keeps the database, settings and logs on the drive, not in the home directory', async () => {
  await launchPortable();
  const layout = resolveLayout(root);

  // Force a settings write so the file is definitely on disk.
  await page.getByTestId('open-settings').click();
  await page.keyboard.press('Escape');
  await expect.poll(() => existsSync(layout.settingsPath), { timeout: 15_000 }).toBe(true);

  expect(existsSync(layout.dbPath)).toBe(true);
  expect(readdirSync(layout.logsDir)).toContain('main.log');
});

test('a conversation started on the drive is still there when the drive is opened again', async () => {
  await launchPortable();

  await page.getByTestId('composer-input').fill('Portable hello');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world', {
    timeout: 20_000,
  });

  await app.close();

  // Same drive, fresh app process — as if it were carried to another machine.
  await launchPortable();
  await expect(page.getByTestId('chat-title')).toHaveText('Portable hello', { timeout: 20_000 });
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
});

test('reports itself as portable, with the drive paths it is actually using', async () => {
  await launchPortable();

  const info = await readPortableInfo(page);
  const layout = resolveLayout(root);

  expect(info.portable).toBe(true);
  expect(info.root).toBe(root);
  expect(info.dataDir).toBe(layout.dataDir);
  expect(info.modelsDir).toBe(layout.modelsDir);
  expect(info.volume?.writable).toBe(true);
  expect(info.volume?.totalBytes).toBeGreaterThan(0);
});

test('shows the drive and its free space in the status bar', async () => {
  await launchPortable();

  const badge = page.getByTestId('portable-badge');
  await expect(badge).toBeVisible({ timeout: 20_000 });
  await expect(badge).toContainText('free');
});

test('reads the label and directory overrides out of the marker file', async () => {
  // A drive provisioned with a different layout, written before first launch.
  mkdirSync(root, { recursive: true });
  writeFileSync(
    path.join(root, PORTABLE_MARKER),
    JSON.stringify({ layoutVersion: 1, label: 'Field kit', directories: { data: 'state' } }),
  );

  await launchPortable();

  const info = await readPortableInfo(page);
  expect(info.label).toBe('Field kit');
  expect(info.dataDir).toBe(path.join(root, 'state'));
  expect(existsSync(path.join(root, 'state', 'ornith.db'))).toBe(true);

  await expect(page.getByTestId('portable-badge')).toContainText('Field kit');
});

test('does not start a second server when one is already answering', async () => {
  await launchPortable();

  // The stub stands in for a machine that already runs Ollama, and this drive
  // carries no runtime binary, so "external" is the only correct answer — and
  // nothing may be spawned against the user's own models.
  const info = await readPortableInfo(page);
  expect(info.runtime.source).toBe('external');
  expect(info.runtime.host).toBe(stub.url);
});

test('an installed launch is unaffected: no drive, no badge, data where it always was', async () => {
  const userData = mkdtempSync(path.join(tmpdir(), 'ornith-installed-'));

  try {
    await open(launchEnv({ ORNITH_USER_DATA: userData }));

    const info = await readPortableInfo(page);
    expect(info.portable).toBe(false);
    expect(info.root).toBeNull();
    expect(info.dataDir).toBe(userData);
    await expect(page.getByTestId('portable-badge')).toHaveCount(0);

    expect(existsSync(path.join(userData, 'ornith.db'))).toBe(true);
    // A directory with no marker is not a drive, and nothing was written into it.
    expect(existsSync(root)).toBe(false);
  } finally {
    rmSync(userData, { recursive: true, force: true });
  }
});

test('starts the Ollama the drive carries when the machine has none, and answers through it', async () => {
  installBundledRuntime();

  // Nothing is listening here, so the external probe misses and the bundled
  // binary is the only way this launch can reach a model.
  const dead = `http://127.0.0.1:${await freePort()}`;
  await open(launchEnv({ ORNITH_PORTABLE_ROOT: root, ORNITH_OLLAMA_URL: dead }));

  await expect
    .poll(() => readPortableInfo(page).then((i) => i.runtime.source), { timeout: 60_000 })
    .toBe('bundled');

  // The configured port was free, so the server takes it rather than drifting
  // to an arbitrary one — the address the user configured keeps working.
  const info = await readPortableInfo(page);
  expect(info.runtime.host).toBe(dead);
  expect(await answers(info.runtime.host)).toBe(true);

  // The whole chain works through the server the drive started.
  await expect(page.getByTestId('status')).toHaveAttribute('data-state', 'ready', {
    timeout: 30_000,
  });
  await page.getByTestId('composer-input').fill('Off the drive');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world', {
    timeout: 20_000,
  });
});

test('gives the bundled server nothing outside the drive to write to', async () => {
  installBundledRuntime();
  const layout = resolveLayout(root);

  await open(
    launchEnv({ ORNITH_PORTABLE_ROOT: root, ORNITH_OLLAMA_URL: `http://127.0.0.1:${await freePort()}` }),
  );
  await expect
    .poll(() => readPortableInfo(page).then((i) => i.runtime.source), { timeout: 60_000 })
    .toBe('bundled');

  // The child recorded the environment it was handed.
  const spawned = JSON.parse(
    readFileSync(path.join(layout.modelsDir, 'spawn-env.json'), 'utf8'),
  ) as Record<string, string | string[]>;

  expect(spawned.argv).toEqual(['serve']);
  expect(spawned.cwd).toBe(root);
  expect(spawned.OLLAMA_MODELS).toBe(layout.modelsDir);
  expect(spawned.HOME).toBe(layout.runtimeHomeDir);
  expect(spawned.OLLAMA_TMPDIR).toBe(layout.runtimeTmpDir);

  // Every path it was pointed at is on the drive, and it is bound to loopback.
  for (const key of ['OLLAMA_MODELS', 'HOME', 'OLLAMA_TMPDIR'] as const) {
    expect(String(spawned[key]).startsWith(root), `${key}=${String(spawned[key])}`).toBe(true);
  }
  expect(String(spawned.OLLAMA_HOST).startsWith('127.0.0.1:')).toBe(true);
});

test('stops the server it started, so the drive can be ejected', async () => {
  installBundledRuntime();

  await open(
    launchEnv({ ORNITH_PORTABLE_ROOT: root, ORNITH_OLLAMA_URL: `http://127.0.0.1:${await freePort()}` }),
  );

  await expect
    .poll(() => readPortableInfo(page).then((i) => i.runtime.source), { timeout: 60_000 })
    .toBe('bundled');

  const host = (await readPortableInfo(page)).runtime.host;
  expect(await answers(host)).toBe(true);

  await app.close();

  await expect.poll(() => answers(host), { timeout: 20_000 }).toBe(false);
});

test('moves to another port when something else already holds the configured one', async () => {
  installBundledRuntime();

  // A service that accepts connections but is not Ollama: the probe fails, so
  // the bundled server must start — somewhere else, because this port is taken.
  const taken = await freePort();
  // destroy, not end: a half-closed socket lingers in the server's connection
  // list and stops close() from ever calling back.
  const squatter = net.createServer((socket) => socket.destroy());
  await new Promise<void>((resolve) => squatter.listen(taken, '127.0.0.1', resolve));

  try {
    await open(
      launchEnv({ ORNITH_PORTABLE_ROOT: root, ORNITH_OLLAMA_URL: `http://127.0.0.1:${taken}` }),
    );

    await expect
      .poll(() => readPortableInfo(page).then((i) => i.runtime.source), { timeout: 60_000 })
      .toBe('bundled');

    const info = await readPortableInfo(page);
    expect(info.runtime.host).not.toBe(`http://127.0.0.1:${taken}`);
    expect(await answers(info.runtime.host)).toBe(true);
  } finally {
    await new Promise<void>((resolve) => squatter.close(() => resolve()));
  }
});
