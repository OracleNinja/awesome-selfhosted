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

/**
 * Renames a conversation through the sidebar's real double-click-to-edit
 * affordance (see Sidebar.tsx), decoupling its title from its message
 * content. `seedConversation` derives the title from the first message
 * verbatim, so a title-only search fixture -- a term present in a title and
 * absent from every message body -- cannot be built from `seedConversation`
 * alone.
 */
async function renameConversation(currentTitle: string, newTitle: string): Promise<void> {
  await page.getByRole('button', { name: currentTitle, exact: true }).dblclick();
  const input = page.locator('.rename-input');
  await input.fill(newTitle);
  await input.press('Enter');
  // Sidebar's commitRename fires the rename through onRename without
  // awaiting it (see Sidebar.tsx), so the rename input disappearing is not
  // proof the store has been updated -- only that the sidebar stopped
  // editing. Waiting for the sidebar button to show the new title is: it only
  // re-renders with it once App.tsx's handleRename has awaited the IPC round
  // trip and refreshed the conversation list.
  await expect(page.getByRole('button', { name: newTitle, exact: true })).toBeVisible();
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

  // P2P-DEFECT-2 (fixed): this conversation's title is derived verbatim from
  // its first message (see electron/chat/orchestrator.ts deriveTitle), so a
  // query matching that text would once have hit both the content and title
  // FTS indexes for the same conversation -- one conversation, two rows. The
  // store now suppresses the title hit whenever a message in the same
  // conversation also matches (electron/store/conversations.ts SEARCH_SQL),
  // so this conversation contributes a single content row. The locator below
  // is strict on purpose -- no .first() -- so resolving to one element is
  // itself an assertion that there is exactly one.
  const unicornResult = page
    .getByTestId('search-result')
    .filter({ hasText: 'The quick unicorn jumps over the fence' });
  await expect(unicornResult).toHaveCount(1);
  await unicornResult.click();

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

/**
 * v2 adds title matching (shared/types.ts SearchMatchField), which is what
 * the tests below exercise: a term that lives only in a conversation's title,
 * never in any message body; a query that matches both kinds at once; and
 * several title hits rendered and selected correctly at once -- the scenario
 * that would catch a renderer key collapsing multiple title hits onto one
 * React key (App.tsx's search results list keys on
 * `${hit.matchedIn}-${hit.messageId ?? hit.conversationId}` rather than the
 * now-optional `hit.messageId` alone).
 */

test('finds a conversation by a term that appears only in its title, never in any message body', async () => {
  // Non-vacuous: two real conversations exist, each with a real message.
  // "ptarmigan" is deliberately absent from both message bodies -- it is
  // typed into the title only, via renameConversation -- so a single
  // title-only hit here is a genuine finding, not an artifact of an empty or
  // near-empty database.
  await seedConversation('quarterly numbers discussion');
  await renameConversation('quarterly numbers discussion', 'Ptarmigan population notes');
  await seedConversation('A giraffe eats leaves all afternoon');

  await triggerSearchMenu();
  await page.getByTestId('search-input').fill('ptarmigan');

  await expect(page.getByTestId('search-result')).toHaveCount(1);
  await expect(page.getByTestId('search-results')).toContainText('Ptarmigan population notes');
  await expect(page.getByTestId('search-results')).not.toContainText('giraffe');
});

test('a query matching both a title and message content returns one hit of each kind', async () => {
  // Two real conversations, each carrying "egret" in exactly one field: the
  // first conversation's title (its message body does not mention it), the
  // second conversation's message body (its title, renamed away from the
  // seeded text, does not mention it either). Non-vacuous for the same reason
  // as above -- both conversations are real and populated.
  await seedConversation('some unrelated body');
  await renameConversation('some unrelated body', 'Egret colony notes');

  await seedConversation('egret feeding behavior observed today');
  await renameConversation('egret feeding behavior observed today', 'Field observations log');

  await triggerSearchMenu();
  await page.getByTestId('search-input').fill('egret');

  await expect(page.getByTestId('search-result')).toHaveCount(2);
  await expect(page.getByTestId('search-results')).toContainText('Egret colony notes');
  await expect(page.getByTestId('search-results')).toContainText('Field observations log');
  await expect(page.getByTestId('search-results')).toContainText('egret feeding behavior observed today');
});

test('multiple title hits render as distinct rows, and selection stays bound to the right conversation as the result set narrows', async () => {
  await seedConversation('unrelated body alpha');
  await renameConversation('unrelated body alpha', 'heron survey north');
  await seedConversation('unrelated body beta');
  await renameConversation('unrelated body beta', 'heron survey south');
  // The third title deliberately does NOT share "survey" with the other two
  // (it shares only "heron"), so a query of "heron survey" narrows the
  // result set to exactly the first two, not down to one -- see below.
  await seedConversation('unrelated body gamma');
  await renameConversation('unrelated body gamma', 'heron count east');

  await triggerSearchMenu();
  await page.getByTestId('search-input').fill('heron');

  // Three distinct rows, each showing its own conversation's title -- this is
  // the literal "renders as distinct rows" check. It does not by itself prove
  // the underlying React keys are unique (three keyless/colliding rows still
  // paint correct text on a first render -- see the handoff notes), which is
  // why the narrow-then-click step below is the part that actually exercises
  // selection against a changing, multi-row result set rather than just a
  // first paint.
  await expect(page.getByTestId('search-result')).toHaveCount(3);
  await expect(
    page.getByTestId('search-result').filter({ hasText: 'heron survey north' }),
  ).toHaveCount(1);
  await expect(
    page.getByTestId('search-result').filter({ hasText: 'heron survey south' }),
  ).toHaveCount(1);
  await expect(
    page.getByTestId('search-result').filter({ hasText: 'heron count east' }),
  ).toHaveCount(1);

  // Narrow to a phrase only the two "survey" conversations satisfy. This
  // deliberately leaves TWO rows, not one: with a single row left, that row
  // *is* hits[0], so "opens whichever row was clicked" would be
  // indistinguishable from a mutation that always opens hits[0] regardless of
  // the click target -- which is exactly the gap a prior version of this
  // test had.
  await page.getByTestId('search-input').fill('heron survey');
  await expect(page.getByTestId('search-result')).toHaveCount(2);

  // Which of the two conversations lands at index 0 vs 1 is not something
  // this test controls: both are structurally identical for the shared terms
  // "heron"/"survey" (same term frequency, same document length, same corpus
  // stats), so their bm25 scores tie and the tiebreak falls back to
  // conversationId -- a random UUID this test never sees. Clicking index 1
  // is what proves the click target is not hits[0] regardless of that
  // ordering; reading that row's own displayed title (rather than asserting
  // a specific one of the two titles is second) is what lets the assertion
  // below be correct regardless of it too.
  const secondRow = page.getByTestId('search-result').nth(1);
  const secondRowTitle = (
    (await secondRow.locator('.search-result-snippet').textContent()) ?? ''
  ).trim();
  expect(['heron survey north', 'heron survey south']).toContain(secondRowTitle);

  await secondRow.click();

  await expect(page.getByTestId('search-dialog')).toHaveCount(0);
  // The conversation that opens must be the one THIS row displayed, not
  // hits[0] -- if selection ignored the click target and always opened the
  // first hit, this would fail whenever the clicked (second) row is not that
  // first hit, which is the case whichever of the two conversations sorts
  // second.
  await expect(page.getByTestId('chat-title')).toHaveText(secondRowTitle);
});

test('selecting a title hit opens that exact conversation and closes the panel', async () => {
  const titles = ['heron survey north', 'heron survey south', 'heron survey east'];
  // Message bodies deliberately share no vocabulary with the titles (unlike
  // the titles themselves, which all share "heron survey"): if a body
  // contained e.g. "heron", that conversation would also produce a *content*
  // hit for the same query, and `.filter({ hasText: title })` below could
  // then match more than one row (the title hit and that content hit both
  // legitimately contain the title text), turning a correctness assertion
  // into a Playwright strict-mode ambiguity instead.
  const bodies = ['unrelated body one', 'unrelated body two', 'unrelated body three'];
  for (let i = 0; i < titles.length; i += 1) {
    await seedConversation(bodies[i]);
    await renameConversation(bodies[i], titles[i]);
  }

  // Each of the three is selected in turn, independent of whatever order the
  // store returns them in (ranking ties break on conversationId, a random
  // UUID this test does not control). A mutation that always opens
  // `hits[0].conversationId` regardless of which row was clicked would pass
  // for whichever title happens to rank first and fail for the other two --
  // looping over all three, rather than picking one arbitrarily, is what
  // makes that failure deterministic instead of a coin flip.
  for (const title of titles) {
    await triggerSearchMenu();
    await page.getByTestId('search-input').fill('heron');
    await expect(page.getByTestId('search-result')).toHaveCount(3);

    await page.getByTestId('search-result').filter({ hasText: title }).click();

    await expect(page.getByTestId('search-dialog')).toHaveCount(0);
    await expect(page.getByTestId('chat-title')).toHaveText(title);
  }
});
