import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startStubOllama, type StubHandle } from '../integration/stubOllama';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

declare global {
  interface Window {
    ornith: {
      settings: {
        get(): Promise<Record<string, unknown> & { gatewayTokenConfigured: boolean }>;
      };
      voice: {
        capabilities(): Promise<{
          stt: { available: boolean; reason?: string };
          tts: { available: boolean; voices: string[]; reason?: string };
        }>;
        speak(req: { requestId: string; text: string; voice: string; rate: number }): Promise<void>;
        stopSpeaking(): Promise<void>;
      };
    };
  }
}

let stub: StubHandle;
let app: ElectronApplication;
let page: Page;
let userData: string;

async function launch(extraEnv: Record<string, string> = {}): Promise<void> {
  app = await electron.launch({
    args: [
      appRoot,
      // Chromium's setuid sandbox is unavailable in this container. This is the
      // OS-level sandbox for the test run only; the app's own
      // webPreferences.sandbox stays true.
      '--no-sandbox',
      '--disable-gpu',
    ],
    env: {
      ...process.env,
      ORNITH_USER_DATA: userData,
      ORNITH_OLLAMA_URL: stub.url,
      NODE_ENV: 'test',
      ...extraEnv,
    },
  });
  page = await app.firstWindow();
  await page.waitForSelector('.app', { timeout: 30_000 });

  // First run blocks the UI until a mode is chosen — that is the privacy
  // guarantee, so the harness answers it rather than bypassing it.
  const chooser = page.getByTestId('mode-chooser');
  if (await chooser.isVisible().catch(() => false)) {
    await page.getByTestId('choose-local').click();
    await expect(chooser).toHaveCount(0);
  }
}

test.beforeEach(async () => {
  userData = mkdtempSync(path.join(tmpdir(), 'ornith-e2e-'));
  stub = await startStubOllama();
  await launch();
});

test.afterEach(async () => {
  await app?.close().catch(() => {});
  await stub?.close();
  rmSync(userData, { recursive: true, force: true });
});

test('asks once for a mode on first run and never sends anything before that', async () => {
  // beforeEach already answered it, so a fresh profile is needed to see it.
  await app.close();
  rmSync(userData, { recursive: true, force: true });
  userData = mkdtempSync(path.join(tmpdir(), 'ornith-e2e-'));

  app = await electron.launch({
    args: [appRoot, '--no-sandbox', '--disable-gpu'],
    env: { ...process.env, ORNITH_USER_DATA: userData, ORNITH_OLLAMA_URL: stub.url, NODE_ENV: 'test' },
  });
  page = await app.firstWindow();
  await page.waitForSelector('.app', { timeout: 30_000 });

  // Blocks the composer until answered.
  await expect(page.getByTestId('mode-chooser')).toBeVisible();
  await page.getByTestId('choose-local').click();
  await expect(page.getByTestId('mode-chooser')).toHaveCount(0);
  await expect(page.getByTestId('mode-label')).toContainText('Local');

  // And is not asked again after a restart.
  await app.close();
  await launch();
  await expect(page.getByTestId('mode-chooser')).toHaveCount(0);
});

test('the gateway token is never exposed to the renderer', async () => {
  await page.getByTestId('open-settings').click();
  await page.getByTestId('settings-gateway-token').fill('super-secret-token-value');
  await page.keyboard.press('Escape');

  // Round-trip it through main and confirm the value does not come back.
  const settings = await page.evaluate(() => window.ornith.settings.get());
  expect(JSON.stringify(settings)).not.toContain('super-secret-token-value');
  expect(settings).not.toHaveProperty('gatewayToken');
  expect(settings.gatewayTokenConfigured).toBe(true);
});

test('launches and detects Ollama and the ornith-en model', async () => {
  const status = page.getByTestId('status');
  await expect(status).toHaveAttribute('data-state', 'ready', { timeout: 20_000 });
  await expect(status).toContainText('ornith-en');
});

test('sends a message, streams a response, and renders it', async () => {
  await page.getByTestId('composer-input').fill('Hello Ornith');
  await page.getByTestId('send-button').click();

  await expect(page.getByTestId('message-user')).toContainText('Hello Ornith');
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world', {
    timeout: 20_000,
  });

  // Title is derived from the first user message.
  await expect(page.getByTestId('chat-title')).toHaveText('Hello Ornith');
});

test('renders markdown and a highlighted python code block with a working copy button', async () => {
  stub.setOptions({
    script: [
      { content: '## Heading\n\nSome **bold** text.\n\n' },
      { content: '```python\nprint("Hello, world!")\n```\n' },
      { content: '\n| a | b |\n|---|---|\n| 1 | 2 |\n' },
      { done: true, eval_count: 4, eval_duration: 1_000_000_000 },
    ],
  });

  await page.getByTestId('composer-input').fill('Write a Python hello world');
  await page.getByTestId('send-button').click();

  await expect(page.locator('.markdown h2')).toHaveText('Heading');
  await expect(page.locator('.markdown strong')).toHaveText('bold');
  await expect(page.locator('.markdown table')).toBeVisible();

  await expect(page.locator('.code-block-lang')).toHaveText('python');
  // Syntax highlighting actually ran, rather than falling back to plain text.
  await expect(page.locator('.code-block-body .hljs-string')).toContainText('Hello, world!');

  await page.getByTestId('copy-code').click();
  await expect(page.getByTestId('copy-code')).toHaveText('Copied');

  const clipboardText = await app.evaluate(({ clipboard }) => clipboard.readText());
  expect(clipboardText).toBe('print("Hello, world!")');
});

test('keeps conversations separate and intact when switching between them', async () => {
  await page.getByTestId('composer-input').fill('First conversation');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  await page.getByTestId('new-chat').click();
  await expect(page.getByTestId('message-list')).toContainText('Ornith Desktop');

  await page.getByTestId('composer-input').fill('Second conversation');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-user')).toContainText('Second conversation');

  // Back to the first.
  await page.getByRole('button', { name: 'First conversation', exact: true }).click();
  await expect(page.getByTestId('message-user')).toContainText('First conversation');
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
});

test('persists conversations across an application restart', async () => {
  await page.getByTestId('composer-input').fill('Remember me');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  await app.close();
  await launch();

  await expect(page.getByTestId('conversation-item').first()).toContainText('Remember me');
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
});

test('stop button halts generation and keeps the partial answer', async () => {
  stub.setOptions({
    script: Array.from({ length: 60 }, (_, i) => ({ content: `word${i} ` })),
    chunkDelayMs: 40,
  });

  await page.getByTestId('composer-input').fill('Count slowly');
  await page.getByTestId('send-button').click();

  await expect(page.getByTestId('stop-button')).toBeVisible();
  await expect(page.getByTestId('message-assistant')).toContainText('word0');

  await page.getByTestId('stop-button').click();

  await expect(page.getByTestId('send-button')).toBeVisible();
  await expect(page.getByTestId('message-stopped')).toBeVisible();
  await expect(page.getByTestId('message-assistant')).toContainText('word0');
});

test('shows reasoning in a collapsible panel with the answer staying primary', async () => {
  stub.setOptions({
    script: [
      { thinking: 'I should greet the user politely.' },
      { content: 'Hello there!' },
      { done: true, eval_count: 2, eval_duration: 1_000_000_000 },
    ],
  });

  await page.getByTestId('composer-input').fill('hi');
  await page.getByTestId('send-button').click();

  await expect(page.getByTestId('message-assistant')).toContainText('Hello there!');

  const toggle = page.getByTestId('thinking-toggle');
  await expect(toggle).toBeVisible();
  // Collapsed by default once the answer has started.
  await expect(page.getByTestId('thinking-body')).toHaveCount(0);

  await toggle.click();
  await expect(page.getByTestId('thinking-body')).toContainText('greet the user politely');

  // Reasoning must not leak into the answer.
  await expect(page.locator('.markdown')).not.toContainText('greet the user politely');

  await toggle.click();
  await expect(page.getByTestId('thinking-body')).toHaveCount(0);
});

test('reports a clear error when Ollama goes away, and recovers when it returns', async () => {
  await expect(page.getByTestId('status')).toHaveAttribute('data-state', 'ready');

  const url = stub.url;
  await stub.close();

  // The status bar polls every 15s, so the drop is detected without any action.
  await expect(page.getByTestId('status')).toHaveAttribute('data-state', 'disconnected', {
    timeout: 30_000,
  });
  // A dead listener yields either ECONNREFUSED or a hung socket depending on
  // timing, so accept both — what matters is that the message is actionable
  // and a Retry control is offered.
  await expect(page.getByTestId('status')).toContainText(/ollama serve|didn't respond in time/);
  await expect(page.getByTestId('retry')).toBeVisible();
  await expect(page.getByTestId('composer-input')).toBeDisabled();

  // Bring the same port back up.
  const port = Number(new URL(url).port);
  stub = await startStubOllama({ port });

  await page.getByTestId('retry').click();
  await expect(page.getByTestId('status')).toHaveAttribute('data-state', 'ready', {
    timeout: 30_000,
  });
  await expect(page.getByTestId('composer-input')).toBeEnabled();

  // And chatting works again without restarting the app.
  await page.getByTestId('composer-input').fill('back online');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
});

/** Replaces the native confirm dialog, which Playwright cannot click. */
async function stubConfirmDialog(choice: 'Cancel' | 'Delete'): Promise<void> {
  await app.evaluate(({ dialog }, response) => {
    dialog.showMessageBox = async () => ({ response, checkboxChecked: false });
  }, choice === 'Delete' ? 1 : 0);
}

test('deletes a conversation after confirmation and it stays deleted after restart', async () => {
  await page.getByTestId('composer-input').fill('Delete me');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  await stubConfirmDialog('Delete');
  await page.getByTestId('conversation-item').first().hover();
  await page.getByTestId('delete-chat').first().click();
  await expect(page.getByTestId('conversation-item')).toHaveCount(0);

  await app.close();
  await launch();
  await expect(page.getByTestId('conversation-item')).toHaveCount(0);
});

test('cancelling the confirm dialog keeps the conversation', async () => {
  await page.getByTestId('composer-input').fill('Keep me');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  await stubConfirmDialog('Cancel');
  await page.getByTestId('conversation-item').first().hover();
  await page.getByTestId('delete-chat').first().click();

  // Still there, and its messages are untouched.
  await expect(page.getByTestId('conversation-item')).toHaveCount(1);
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
});

test('Escape stops generation without a menu accelerator', async () => {
  stub.setOptions({
    script: Array.from({ length: 60 }, (_, i) => ({ content: `word${i} ` })),
    chunkDelayMs: 40,
  });

  await page.getByTestId('composer-input').fill('Count slowly');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('stop-button')).toBeVisible();
  await expect(page.getByTestId('message-assistant')).toContainText('word0');

  // Focus must be off the composer, or Escape belongs to the text field.
  await page.locator('.messages').click();
  await page.keyboard.press('Escape');

  await expect(page.getByTestId('send-button')).toBeVisible();
  await expect(page.getByTestId('message-stopped')).toBeVisible();
});

test('labels an unclosed reasoning block instead of discarding the text', async () => {
  // Relaunch against a server with no structured `think` support, so the
  // renderer exercises the inline parser path.
  await app.close();
  await stub.close();
  stub = await startStubOllama({
    supportsThink: false,
    script: [
      { content: '<think>reasoning that never closes' },
      { done: true, eval_count: 5, eval_duration: 1_000_000_000 },
    ],
  });
  await launch();

  await page.getByTestId('composer-input').fill('think out loud');
  await page.getByTestId('send-button').click();

  const toggle = page.getByTestId('thinking-toggle');
  await expect(toggle).toContainText('Incomplete reasoning');
  // Expanded by default, and the text is preserved rather than dropped.
  await expect(page.getByTestId('thinking-body')).toContainText('reasoning that never closes');
  // An unclosed block is a presentation flag, not a failed turn.
  await expect(page.getByTestId('message-error')).toHaveCount(0);
});

test('labels an unclosed reasoning block as incomplete even when the done chunk arrives late', async () => {
  // Same scenario as the test above, but the reasoning chunk and the `done`
  // chunk are deliberately forced into separate ticks via chunkDelayMs. This
  // is what a loaded machine makes every stream look like — the previous
  // test only catches this defect when the two chunks happen to land in the
  // same React render batch, which is exactly how it was misfiled as flaky
  // (P2Q-DEFECT-1). chunkDelayMs controls the stub's own timing, not the
  // assertions below: the `expect` calls still poll for the final state,
  // they do not sleep and hope.
  await app.close();
  await stub.close();
  stub = await startStubOllama({
    supportsThink: false,
    chunkDelayMs: 400,
    script: [
      { content: '<think>reasoning that never closes' },
      { done: true, eval_count: 5, eval_duration: 1_000_000_000 },
    ],
  });
  await launch();

  await page.getByTestId('composer-input').fill('think out loud');
  await page.getByTestId('send-button').click();

  const toggle = page.getByTestId('thinking-toggle');
  await expect(toggle).toContainText('Incomplete reasoning');
  await expect(page.getByTestId('thinking-body')).toContainText('reasoning that never closes');
  await expect(page.getByTestId('message-error')).toHaveCount(0);
});

test('keeps reasoning expanded once the answer starts when "Always expand reasoning" is on', async () => {
  // Regression coverage for a setting that had no test at all: a formula
  // that looked plausible for the incomplete-reasoning fix above silently
  // broke this setting by collapsing the panel the moment the answer
  // started, which is exactly when a user who turned this on would want to
  // read it.
  await page.getByTestId('open-settings').click();

  // Not `.check()`: this checkbox is controlled and its value round-trips
  // through IPC (onChange -> main process -> persisted -> new prop), so the
  // render that carries the new `checked` value lands a tick after the
  // click. `.check()` clicks and verifies in the same breath and can observe
  // the pre-update render, failing intermittently even though the click
  // worked. Asserting `toBeChecked()` separately checks the same fact but
  // polls for it instead of sampling once — same shape as the production
  // bug this file is testing, so it gets the same treatment here.
  const showThinking = page.getByTestId('settings-show-thinking');
  await showThinking.click();
  await expect(showThinking).toBeChecked();

  await page.keyboard.press('Escape');
  await expect(page.getByTestId('settings-dialog')).toHaveCount(0);

  stub.setOptions({
    script: [
      { thinking: 'I should greet the user politely.' },
      { content: 'Hello there!' },
      { done: true, eval_count: 2, eval_duration: 1_000_000_000 },
    ],
  });

  await page.getByTestId('composer-input').fill('hi');
  await page.getByTestId('send-button').click();

  await expect(page.getByTestId('message-assistant')).toContainText('Hello there!');

  // Still expanded after the answer has fully rendered, unlike the default
  // (collapsed) behaviour covered by the "shows reasoning in a collapsible
  // panel" test above.
  const toggle = page.getByTestId('thinking-toggle');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByTestId('thinking-body')).toContainText('greet the user politely');
});

test('settings changes apply immediately and survive a restart', async () => {
  await page.getByTestId('open-settings').click();
  await expect(page.getByTestId('settings-dialog')).toBeVisible();

  await page.getByTestId('settings-numctx').fill('16384');
  await page.getByTestId('settings-theme').selectOption('light');

  // Theme applies without a restart.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.keyboard.press('Escape');
  await expect(page.getByTestId('settings-dialog')).toHaveCount(0);

  await app.close();
  await launch();

  await page.getByTestId('open-settings').click();
  await expect(page.getByTestId('settings-numctx')).toHaveValue('16384');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('the mic button degrades gracefully where speech is unavailable', async () => {
  const mic = page.getByTestId('mic-button');
  await expect(mic).toBeVisible();

  const capabilities = await page.evaluate(() => window.ornith.voice.capabilities());

  if (capabilities.stt.available) {
    await expect(mic).toBeEnabled();
  } else {
    // This is the real path on any non-macOS host: disabled, with a reason
    // the user can act on rather than a dead control or a crash.
    await expect(mic).toBeDisabled();
    expect(capabilities.stt.reason).toBeTruthy();
    await expect(mic).toHaveAttribute('title', capabilities.stt.reason!);
  }

  // Chatting must be unaffected either way.
  await page.getByTestId('composer-input').fill('voice off, typing still fine');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
});

test('speaking a response is skipped when text-to-speech is unavailable', async () => {
  const capabilities = await page.evaluate(() => window.ornith.voice.capabilities());

  // Requesting speech must never throw, even with no engine present.
  const threw = await page.evaluate(async () => {
    try {
      await window.ornith.voice.speak({ requestId: 'e2e', text: 'hello', voice: '', rate: 175 });
      await window.ornith.voice.stopSpeaking();
      return false;
    } catch {
      return true;
    }
  });

  expect(threw).toBe(false);
  if (!capabilities.tts.available) expect(capabilities.tts.voices).toEqual([]);
});

test('the renderer has no Node access and makes no network requests', async () => {
  const exposure = await page.evaluate(() => {
    const w = window as unknown as { require?: unknown; process?: unknown; ornith?: unknown };
    return {
      hasRequire: typeof w.require !== 'undefined',
      hasProcess: typeof w.process !== 'undefined',
      hasBridge: typeof w.ornith === 'object',
    };
  });

  expect(exposure.hasRequire).toBe(false);
  expect(exposure.hasProcess).toBe(false);
  expect(exposure.hasBridge).toBe(true);

  // The renderer must not be able to reach Ollama directly.
  const requests: string[] = [];
  page.on('request', (r) => requests.push(r.url()));

  await page.getByTestId('composer-input').fill('no network please');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');

  expect(requests.filter((u) => u.includes('11434') || u.includes(stub.url))).toHaveLength(0);
});
