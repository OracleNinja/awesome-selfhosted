import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startStubOllama, type StubHandle } from '../integration/stubOllama';

/**
 * Exercises Find in Conversations end to end: the native menu click, the
 * debounced search panel in App.tsx, and the real `conv:search` round trip
 * through electron/search.ts and the FTS5-backed store.
 *
 * The menu item is triggered via `Menu.getApplicationMenu()` in the main
 * process, exactly like tests/e2e/export.spec.ts: Playwright's
 * `page.keyboard` dispatches into the renderer's web content, not Electron's
 * native accelerator table, so a synthetic Cmd/Ctrl+F cannot be trusted to
 * fire the accelerator. `menu.ts` gives the item a stable `id`
 * (`menu-search`) for exactly this reason.
 */

/**
 * SNIPPET_MATCH_OPEN / SNIPPET_MATCH_CLOSE from shared/types.ts (0xE000 /
 * 0xE001), built via fromCharCode rather than a literal private-use
 * character so the test source has no invisible glyphs in it.
 */
const SNIPPET_MATCH_OPEN = String.fromCharCode(0xe000);
const SNIPPET_MATCH_CLOSE = String.fromCharCode(0xe001);

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

let stub: StubHandle;
let app: ElectronApplication;
let page: Page;
let userData: string;

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

/** Clicks the real Edit > Find in Conversations… menu item from the main process. */
async function triggerSearchMenu(): Promise<void> {
  await app.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById('menu-search')?.click();
  });
}

/**
 * Sends `text` as the first message of a brand-new conversation. The title is
 * derived from the first user message (see app.spec.ts), which is what makes
 * the seeded conversations distinguishable in the results list.
 */
async function seedConversation(text: string): Promise<void> {
  const existing = await page.getByTestId('conversation-item').count();
  if (existing > 0) {
    await page.getByTestId('new-chat').click();
    // handleNewChat is async (it may create a conversation over an IPC round
    // trip) and the composer's draft is keyed by the active conversation id.
    // Filling immediately after the click can land on the *previous*
    // conversation's draft, which is then wiped the instant the new (empty)
    // conversation becomes active — leaving the send button disabled when
    // this helper clicks it below. Waiting for the empty-conversation state
    // to render first guarantees the new conversation is already active, the
    // same wait app.spec.ts's "keeps conversations separate" test relies on
    // for this identical seeding step.
    await expect(page.getByTestId('message-list')).toContainText('Ornith Desktop');
  }

  await page.getByTestId('composer-input').fill(text);
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('message-assistant')).toContainText('Hello world');
  // The assistant's text can land in the DOM slightly before `chat:end`
  // actually fires: chat:delta streams "Hello" then " world" as separate
  // IPC events, and only the following `done` chunk triggers chat:end, which
  // is what flips the composer from Stop back to Send. Waiting for the text
  // alone is not enough to know this turn is over — if the *next*
  // seedConversation call clicks "New Chat" while a request is technically
  // still in flight, handleNewChat aborts it out from under this one. The
  // Send button reappearing is the same "generation has truly stopped"
  // signal app.spec.ts's stop-generation test waits on.
  await expect(page.getByTestId('send-button')).toBeVisible();
}

test.beforeEach(async () => {
  userData = mkdtempSync(path.join(tmpdir(), 'ornith-search-e2e-'));
  stub = await startStubOllama();
  await launch();
});

test.afterEach(async () => {
  await app?.close().catch(() => {});
  await stub?.close();
  rmSync(userData, { recursive: true, force: true });
});

test('finds a conversation by content and does not show a conversation without the term', async () => {
  await seedConversation('The quick unicorn jumps over the fence');
  await seedConversation('A giraffe eats leaves all afternoon');

  await triggerSearchMenu();
  await expect(page.getByTestId('search-dialog')).toBeVisible();

  await page.getByTestId('search-input').fill('unicorn');

  const results = page.getByTestId('search-results');
  await expect(results).toContainText('The quick unicorn jumps over the fence');
  await expect(results).not.toContainText('A giraffe eats leaves all afternoon');
});

test('selecting a result opens the matching conversation and closes the panel', async () => {
  await seedConversation('The quick unicorn jumps over the fence');
  await seedConversation('A giraffe eats leaves all afternoon');

  // Currently on the second (giraffe) conversation.
  await expect(page.getByTestId('chat-title')).toHaveText('A giraffe eats leaves all afternoon');

  await triggerSearchMenu();
  await page.getByTestId('search-input').fill('unicorn');

  await page
    .getByTestId('search-result')
    .filter({ hasText: 'The quick unicorn jumps over the fence' })
    .click();

  // The panel closes and the app actually switches conversations — checked
  // through what a user would see, not internal state.
  await expect(page.getByTestId('search-dialog')).toHaveCount(0);
  await expect(page.getByTestId('chat-title')).toHaveText('The quick unicorn jumps over the fence');
  await expect(page.getByTestId('message-user')).toContainText('The quick unicorn jumps over the fence');
});

test('an empty or whitespace query shows no results and never claims "no results"', async () => {
  await seedConversation('Something perfectly searchable');

  await triggerSearchMenu();
  await expect(page.getByTestId('search-dialog')).toBeVisible();

  // Freshly opened: nothing typed yet.
  await expect(page.getByTestId('search-results')).toHaveCount(0);
  await expect(page.getByTestId('search-empty')).toHaveCount(0);

  await page.getByTestId('search-input').fill('   ');
  // Let the debounce window pass; there is no positive signal for "nothing
  // happened", so this waits out the interval before asserting its absence.
  await page.waitForTimeout(500);

  await expect(page.getByTestId('search-results')).toHaveCount(0);
  await expect(page.getByTestId('search-empty')).toHaveCount(0);
});

test('a query with no matches shows the empty-result state', async () => {
  await seedConversation('Something perfectly searchable');

  await triggerSearchMenu();
  await page.getByTestId('search-input').fill('zzznomatchforthiszzz');

  await expect(page.getByTestId('search-empty')).toBeVisible();
  await expect(page.getByTestId('search-results')).toHaveCount(0);
});

test('renders the snippet with the match highlighted and no private-use markers visible', async () => {
  await seedConversation('The nightingale sings at dusk every single evening');

  await triggerSearchMenu();
  await page.getByTestId('search-input').fill('nightingale');

  const row = page.getByTestId('search-result').first();
  await expect(row).toBeVisible();

  // The matched run is real highlighted markup, not raw delimiter text.
  await expect(row.locator('mark')).toHaveText(/nightingale/i);

  // The private-use delimiters must never reach the rendered text — see
  // shared/types.ts and App.tsx's renderSnippet, which strips them and
  // renders plain text nodes instead of using dangerouslySetInnerHTML.
  const rowText = await row.textContent();
  expect(rowText ?? '').not.toContain(SNIPPET_MATCH_OPEN);
  expect(rowText ?? '').not.toContain(SNIPPET_MATCH_CLOSE);
});

test('Escape closes the search panel', async () => {
  await seedConversation('Closable with the escape key');

  await triggerSearchMenu();
  await expect(page.getByTestId('search-dialog')).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(page.getByTestId('search-dialog')).toHaveCount(0);
});
