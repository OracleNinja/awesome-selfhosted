import type { DatabaseSync } from 'node:sqlite';

/**
 * Deterministic, seeded search corpus for performance testing
 * (tests/integration/search-performance.test.ts). No real user data: every
 * conversation title and message body is built from a small fixed vocabulary
 * by a self-contained PRNG.
 *
 * Determinism, by construction:
 *  1. One fixed numeric seed (`SEARCH_CORPUS_SEED`), never derived from
 *     `Date.now()`, env vars, argv, or the host.
 *  2. One PRNG stream (mulberry32, defined below, no `Math.random` anywhere
 *     in this file). Every choice -- title word, title length, message count,
 *     message word, message length -- is one draw from that stream, consumed
 *     in a fixed order determined by this code, never by object-key or
 *     filesystem iteration order.
 *  3. IDs are deterministic templates (`conv-000000`, `msg-0000000`), not
 *     `crypto.randomUUID()`.
 *  4. Timestamps are `BASE_TS + i * <fixed spacing>`, never `Date.now()` or
 *     `new Date()` with no argument.
 *
 * Consequence: the same seed always produces byte-identical rows, so the
 * resulting database's content -- and therefore SQLite's query-plan choice
 * for a fixed schema -- is stable across runs and machines.
 */

export const SEARCH_CORPUS_SEED = 133742;

// Tuned to land near the Lead's reference corpus (~540 conversations / ~5,600
// messages). This is an approximation, not a verified exact total: nothing in
// this worktree can execute the generator to confirm the resulting count, so
// treat "~5,600" as a target the range below was chosen to approximate
// (540 conversations x an average of 10.5 messages ~= 5,670), not a
// guarantee.
const CONVERSATION_COUNT = 540;
const MIN_MESSAGES_PER_CONVERSATION = 6;
const MAX_MESSAGES_PER_CONVERSATION = 15;

const BASE_TS = Date.UTC(2024, 0, 1);
const CONVERSATION_SPACING_MS = 3_600_000; // 1 hour apart -- arbitrary, but fixed
const MESSAGE_SPACING_MS = 60_000; // 1 minute apart within a conversation

// Ornithology-survey vocabulary, matching the app's own domain (and the
// Lead's own "heron survey" reproduction) rather than any real user data.
const TITLE_WORDS = [
  'heron', 'survey', 'wetland', 'migration', 'estuary', 'canopy', 'tide',
  'reed', 'marsh', 'plover', 'osprey', 'falcon', 'kestrel', 'linnet',
  'wryneck', 'dunlin', 'shrike', 'bittern', 'egret', 'ptarmigan', 'quokka',
  'meadow', 'harbour', 'notes', 'log', 'planning', 'session', 'colony',
  'retrospective', 'north', 'south', 'east', 'west',
] as const;

// Overlaps with TITLE_WORDS on purpose, so a term can plausibly land in both
// a title and a message body -- the shape a "mixed" search result exercises.
const CONTENT_WORDS = [
  ...TITLE_WORDS,
  'the', 'a', 'an', 'over', 'under', 'near', 'along', 'across', 'today',
  'yesterday', 'observed', 'counted', 'recorded', 'flock', 'nest', 'feeding',
  'breeding', 'roost', 'dawn', 'dusk', 'weather', 'wind', 'rain', 'sunny',
  'quiet', 'busy', 'population', 'behaviour', 'pattern', 'route', 'site',
  'visit', 'team', 'volunteer', 'report', 'update', 'follow', 'discussed',
  'plan', 'meeting', 'summary', 'result', 'number', 'total', 'change',
] as const;

/** mulberry32: a small, self-contained, deterministic 32-bit PRNG. No Math.random. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function randInt(rng: () => number, min: number, max: number): number {
  return min + Math.floor(rng() * (max - min + 1));
}

function pick<T>(rng: () => number, items: readonly T[]): T {
  return items[randInt(rng, 0, items.length - 1)];
}

function words(rng: () => number, bank: readonly string[], count: number): string {
  const out: string[] = [];
  for (let i = 0; i < count; i += 1) out.push(pick(rng, bank));
  return out.join(' ');
}

export interface GeneratedConversation {
  id: string;
  title: string;
  model: string;
  createdAt: number;
  updatedAt: number;
}

export interface GeneratedMessage {
  id: string;
  conversationId: string;
  seq: number;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
}

export interface SearchCorpus {
  conversations: GeneratedConversation[];
  messages: GeneratedMessage[];
}

export function generateSearchCorpus(seed: number = SEARCH_CORPUS_SEED): SearchCorpus {
  const rng = mulberry32(seed);
  const conversations: GeneratedConversation[] = [];
  const messages: GeneratedMessage[] = [];
  let messageCounter = 0;

  for (let i = 0; i < CONVERSATION_COUNT; i += 1) {
    const convId = `conv-${String(i).padStart(6, '0')}`;
    const createdAt = BASE_TS + i * CONVERSATION_SPACING_MS;
    const title = words(rng, TITLE_WORDS, randInt(rng, 2, 4));
    const messageCount = randInt(rng, MIN_MESSAGES_PER_CONVERSATION, MAX_MESSAGES_PER_CONVERSATION);

    for (let j = 0; j < messageCount; j += 1) {
      const msgId = `msg-${String(messageCounter).padStart(7, '0')}`;
      messageCounter += 1;
      messages.push({
        id: msgId,
        conversationId: convId,
        seq: j,
        role: j % 2 === 0 ? 'user' : 'assistant',
        content: words(rng, CONTENT_WORDS, randInt(rng, 6, 14)),
        createdAt: createdAt + j * MESSAGE_SPACING_MS,
      });
    }

    conversations.push({
      id: convId,
      title,
      model: 'ornith-en',
      createdAt,
      updatedAt: createdAt + Math.max(0, messageCount - 1) * MESSAGE_SPACING_MS,
    });
  }

  return { conversations, messages };
}

/**
 * Inserts directly into the base `conversations`/`messages` tables via
 * parameterized statements. Deliberately not `ConversationStore.create()` /
 * `beginTurn()`: those use `crypto.randomUUID()` and `Date.now()`
 * internally, which would reintroduce nondeterminism into an otherwise fully
 * seeded corpus. `messages_fts` / `conversations_fts` are populated
 * automatically by the AFTER INSERT triggers already defined in
 * electron/store/db.ts -- this function does not, and must not, write to
 * either FTS table directly.
 */
export function insertSearchCorpus(db: DatabaseSync, corpus: SearchCorpus): void {
  const insertConversation = db.prepare(
    'INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
  );
  const insertMessage = db.prepare(
    `INSERT INTO messages (id, conversation_id, seq, role, content, thinking, status, created_at)
     VALUES (?, ?, ?, ?, ?, '', 'complete', ?)`,
  );

  db.exec('BEGIN');
  try {
    for (const c of corpus.conversations) {
      insertConversation.run(c.id, c.title, c.model, c.createdAt, c.updatedAt);
    }
    for (const m of corpus.messages) {
      insertMessage.run(m.id, m.conversationId, m.seq, m.role, m.content, m.createdAt);
    }
    db.exec('COMMIT');
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
}
