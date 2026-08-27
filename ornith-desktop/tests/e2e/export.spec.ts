import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startStubOllama, type StubHandle } from '../integration/stubOllama';

/**
 * Exercises the File > Export Chat… menu item end to end: the native menu
 * click, the in-app format/reasoning chooser (see App.tsx — `menu:export-chat`
 * carries no payload, so the format choice has to be made in the renderer),
 * and the real main-process write path in electron/export.ts.
 *
 * A native save dialog cannot be driven by Playwright, so `showSaveDialog` is
 * replaced at test time via `ElectronApplication.evaluate()`, which runs code
 * in the main process. This is a test-only substitution made from outside the
 * app; production code is untouched, and no test hook or env var was added to
 * electron/export.ts.
 *
 * The menu item itself is triggered the same way, via `Menu.getApplicationMenu()`
 * in the main process: Playwright's `page.keyboard` dispatches into the
 * renderer's web content, not Electron's native accelerator table, so a
 * synthetic keypress cannot be trusted to fire a menu accelerator. `menu.ts`
 * gives the item a stable `id` (`menu-export-chat`) for exactly this reason.
 */

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

let stub: StubHandle;
let app: ElectronApplication;
let page: Page;
let userData: string;
let exportDir: string;

async function launch(): Promise<void> {
  app = await electron.launch({
    args: [appRoot, '--no-sandbox', '--disable-gpu'],
    env: {
      ...process.env,
      ORNITH_USER_DATA: userData,
      ORNITH_OLLAMA_URL: stub.url,
      NODE_ENV: 'test',
    },
  });
  page = await app.firstWindow();
  await page.waitForSelector('.app', { timeout: 30_000 });

  const chooser = page.getByTestId('mode-chooser');
  if (await chooser.isVisible().catch(() => false)) {
    await page.getByTestId('choose-local').click();
    await expect(chooser).toHaveCount(0);
  }
}

/** Clicks the real File > Export Chat… menu item from the main process. */
async function triggerExportMenu(): Promise<void> {
  await app.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById('menu-export-chat')?.click();
  });
}

/** Replaces the native save dialog for the remainder of this app instance. */
async function stubSaveDialog(filePath: string): Promise<void> {
  await app.evaluate(({ dialog }, target) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (dialog as any).showSaveDialog = async () => ({ canceled: false, filePath: target });
  }, filePath);
}

async function stubCancelledSaveDialog(): Promise<void> {
  await app.evaluate(({ dialog }) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (dialog as any).showSaveDialog = async () => ({ canceled: true, filePath: '' });
  });
}

test.beforeEach(async () => {
  userData = mkdtempSync(path.join(tmpdir(), 'ornith-export-e2e-'));
  exportDir = mkdtempSync(path.join(tmpdir(), 'ornith-export-target-'));
  stub = await startStubOllama();
  await launch();
});

test.afterEach(async () => {
  await app?.close().catch(() => {});
  await stub?.close();
  rmSync(userData, { recursive: true, force: true });
  rmSync(exportDir, { recursive: true, force: true });
});

test('exports the active conversation to disk as Markdown and confirms quietly', async () => {
  await page.getByTestId('composer-input').fill('Export me please');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  const target = path.join(exportDir, 'export.md');
  await stubSaveDialog(target);

  await triggerExportMenu();
  await expect(page.getByTestId('export-chat-dialog')).toBeVisible();

  await page.getByTestId('export-as-markdown').click();

  // Quiet confirmation: the chooser simply closes, with no error shown.
  await expect(page.getByTestId('export-chat-dialog')).toHaveCount(0);

  const content = readFileSync(target, 'utf8');
  expect(content).toContain('# Export me please');
  expect(content).toContain('Hello world');
});

test('exports as JSON and includes reasoning only when the checkbox is checked', async () => {
  await page.getByTestId('composer-input').fill('Export me as JSON');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  const withoutReasoning = path.join(exportDir, 'no-reasoning.json');
  await stubSaveDialog(withoutReasoning);
  await triggerExportMenu();
  await expect(page.getByTestId('export-chat-dialog')).toBeVisible();
  await page.getByTestId('export-as-json').click();
  await expect(page.getByTestId('export-chat-dialog')).toHaveCount(0);

  const plain = JSON.parse(readFileSync(withoutReasoning, 'utf8')) as {
    messages: Record<string, unknown>[];
  };
  expect(plain.messages.length).toBeGreaterThan(0);
  expect(plain.messages.some((m) => 'thinking' in m)).toBe(false);

  const withReasoningPath = path.join(exportDir, 'with-reasoning.json');
  await stubSaveDialog(withReasoningPath);
  await triggerExportMenu();
  await expect(page.getByTestId('export-chat-dialog')).toBeVisible();
  await page.getByTestId('export-include-reasoning').check();
  await page.getByTestId('export-as-json').click();
  await expect(page.getByTestId('export-chat-dialog')).toHaveCount(0);

  const withReasoning = JSON.parse(readFileSync(withReasoningPath, 'utf8')) as {
    messages: Record<string, unknown>[];
  };
  expect(withReasoning.messages.some((m) => 'thinking' in m)).toBe(true);
});

test('cancelling the save dialog writes nothing and shows no error', async () => {
  await page.getByTestId('composer-input').fill('Do not save me');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  await stubCancelledSaveDialog();

  await triggerExportMenu();
  await expect(page.getByTestId('export-chat-dialog')).toBeVisible();
  await page.getByTestId('export-as-markdown').click();

  // Cancelling is a deliberate act, not a failure: the chooser closes with
  // nothing alarming shown, and nothing is written to disk.
  await expect(page.getByTestId('export-chat-dialog')).toHaveCount(0);
  await expect(page.getByTestId('export-error')).toHaveCount(0);
  expect(readdirSync(exportDir)).toHaveLength(0);
});

test('shows the error message as-is when the save fails, without embellishment', async () => {
  await page.getByTestId('composer-input').fill('This save will fail');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  // A path inside a non-existent parent directory fails deterministically
  // (ENOENT) regardless of the machine running the test — the same technique
  // tests/integration/export.test.ts uses for the equivalent unit-level case.
  const target = path.join(exportDir, 'missing-subdir', 'export.md');
  await stubSaveDialog(target);

  await triggerExportMenu();
  await expect(page.getByTestId('export-chat-dialog')).toBeVisible();
  await page.getByTestId('export-as-markdown').click();

  const error = page.getByTestId('export-error');
  await expect(error).toBeVisible();
  const message = await error.textContent();
  expect(message).toBeTruthy();
  expect(message).not.toMatch(/ENOENT/i);
  expect(message).not.toContain(exportDir);

  // The chooser stays open on error, so retrying doesn't require reopening
  // the menu; nothing was written.
  await expect(page.getByTestId('export-chat-dialog')).toBeVisible();
  expect(existsSync(target)).toBe(false);
});

test('exporting with no active conversation does nothing and never opens the chooser', async () => {
  // Fresh profile from beforeEach: no message has been sent, so there is no
  // active conversation yet.
  await expect(page.getByTestId('conversation-item')).toHaveCount(0);

  const pageErrors: Error[] = [];
  page.on('pageerror', (err) => pageErrors.push(err));

  await triggerExportMenu();

  // No positive signal exists for "nothing happened", so a short grace
  // period lets any synchronous/microtask exception surface before asserting
  // its absence.
  await page.waitForTimeout(300);

  expect(pageErrors).toHaveLength(0);
  await expect(page.getByTestId('export-chat-dialog')).toHaveCount(0);

  // The app remains fully usable afterwards.
  await page.getByTestId('composer-input').fill('still alive');
  await expect(page.getByTestId('composer-input')).toHaveValue('still alive');
});
