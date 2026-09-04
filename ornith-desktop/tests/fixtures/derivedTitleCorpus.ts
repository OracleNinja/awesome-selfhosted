import type { ConversationStore } from '../../electron/store/conversations';
import { deriveTitle } from '../../shared/model';

/**
 * P2P-DEFECT-2 regression fixtures.
 *
 * Every existing search fixture (tests/fixtures/searchCorpus.ts's generated
 * corpus aside, whose title/content vocabulary overlap is coincidental, not
 * structural; and the isolated-title cases in tests/unit/search.test.ts, e.g.
 * `store.create('ornith-en', 'Nightjar planning session')` followed by an
 * unrelated message) deliberately keeps a conversation's title term and its
 * message-content term apart. That is good discipline for testing the two
 * FTS5 indexes independently, but it is exactly what let the duplicate-row
 * defect ship undetected: in production, electron/chat/orchestrator.ts sets
 * a new conversation's title to `deriveTitle(firstMessage)`, so the title IS
 * a derivative of a message that is itself indexed by `messages_fts`. This
 * module builds conversations that shape -- title and content genuinely
 * overlapping -- the way the app actually produces them.
 *
 * Two real app paths are represented here, not one:
 *
 *  - `seedAppDerivedConversation` mirrors orchestrator.ts's default path for
 *    a first turn: create -> beginTurn(firstMessage) ->
 *    rename(deriveTitle(firstMessage)). Title and the first message always
 *    overlap here -- that overlap is the defect case, not a fixture
 *    artifact.
 *
 *  - `seedManuallyTitledConversation` mirrors the app's *other* real path to
 *    a title: a user manually renaming a conversation through the sidebar
 *    (`conv:rename`, electron/main.ts) via `ConversationStore.rename`. This
 *    is the only way a title-only match can exist at all: `deriveTitle` can
 *    only ever remove words from the first message (stripping code-fence
 *    content and markdown syntax) or truncate it -- see shared/model.ts --
 *    never introduce a word that was not already in that message. A title
 *    produced by pure auto-derivation can therefore never contain a query
 *    term absent from that same message's already-indexed content, so a
 *    genuine title-only hit cannot be built from `seedAppDerivedConversation`
 *    alone. This is not fixture convenience; it is what makes a title-only
 *    hit possible in the first place, and it is called out here so the next
 *    reader does not mistake it for one.
 *
 * Determinism: ids and timestamps come from `ConversationStore` itself
 * (`crypto.randomUUID()` / `Date.now()` inside `create`/`beginTurn`), so
 * callers assert against the ids these functions return, never against a
 * guessed value -- the same pattern `tests/unit/conversations.test.ts`
 * already uses (e.g. `expect(list[0].id).toBe(a.id)`). Every message/title
 * string passed in is a literal constant chosen by the caller, so query
 * terms and expected match kinds are fully deterministic. No real user data.
 *
 * Disposable: this module holds no module-level state; every function takes
 * the store as a parameter and returns fresh ids, so it cannot leak state
 * between tests even across a shared `DatabaseSync`. Existing tests in this
 * suite create a fresh `openDatabase(':memory:')` per test, so in practice
 * nothing here outlives the test that seeded it.
 */

export interface AppDerivedConversation {
  conversationId: string;
  /** The title the app would have derived and set via rename(). */
  title: string;
  firstMessageId: string;
  /** In the order `followUpMessages` was given. */
  followUpMessageIds: string[];
}

/**
 * Builds a conversation exactly the way orchestrator.ts's `start()` does for
 * a first turn: create -> beginTurn(firstMessage) -> rename(deriveTitle(...)).
 * Any `followUpMessages` are appended as later turns via further
 * `beginTurn` calls; the title is fixed after the first turn and does not
 * change for them, mirroring orchestrator.ts only deriving a title when
 * `isFirstTurn`.
 */
export function seedAppDerivedConversation(
  store: ConversationStore,
  model: string,
  firstMessage: string,
  followUpMessages: readonly string[] = [],
): AppDerivedConversation {
  const conversation = store.create(model);
  const { userMessage: firstUserMessage } = store.beginTurn(conversation.id, firstMessage, model);

  const title = deriveTitle(firstMessage);
  store.rename(conversation.id, title);

  const followUpMessageIds = followUpMessages.map((text) => {
    const { userMessage } = store.beginTurn(conversation.id, text, model);
    return userMessage.id;
  });

  return {
    conversationId: conversation.id,
    title,
    firstMessageId: firstUserMessage.id,
    followUpMessageIds,
  };
}

export interface ManuallyTitledConversation {
  conversationId: string;
  title: string;
}

/**
 * Builds a conversation whose title is set independently of any message's
 * text -- the manual-rename app path, not auto-derivation. Deliberately
 * `create()` with no title, then a separate `rename()` call, mirroring the
 * real `conv:create` -> `conv:rename` IPC sequence (electron/main.ts:247-252:
 * `conv:create` always calls `store.create(model)` with no second argument,
 * defaulting to 'New chat'; only `conv:rename` ever sets an arbitrary
 * title). `store.create(model, title)`'s two-argument form is a
 * test-convenience shortcut that production code never actually calls, so
 * using it here would have quietly reintroduced a test-only shape into the
 * one fixture meant to prove this shape is real. See the module comment
 * above for why manual rename is the only real path to a title-only match.
 */
export function seedManuallyTitledConversation(
  store: ConversationStore,
  model: string,
  title: string,
  messages: readonly string[],
): ManuallyTitledConversation {
  const conversation = store.create(model);
  store.rename(conversation.id, title);
  for (const text of messages) {
    store.beginTurn(conversation.id, text, model);
  }
  return { conversationId: conversation.id, title };
}
