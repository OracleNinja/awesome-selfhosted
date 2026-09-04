import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { DatabaseSync } from 'node:sqlite';
import type { BrowserWindow, SaveDialogOptions, SaveDialogReturnValue } from 'electron';
import { describe, expect, it, vi } from 'vitest';

import { openDatabase } from '../../electron/store/db';
import { createConversationStore, type ConversationStore } from '../../electron/store/conversations';
import {
  exportConversation,
  type ConversationLookup,
  type ExportDeps,
  type ShowSaveDialog,
} from '../../electron/export';
import { formatConversation, suggestFilename } from '../../shared/exportFormat';

/**
 * Real fixtures, not mocks: an in-memory `node:sqlite` conversation store
 * (the same one `orchestrator.test.ts` uses) and a real temp directory on
 * disk, so the write path is exercised against the actual filesystem.
 *
 * The `try` opens before the first fixture (`dir`) is created, so its
 * `finally` always cleans up — the temp directory and the database — even if
 * a later fixture or the test body throws.
 */
interface Fixtures {
  dir: string;
  store: ConversationStore;
}

async function withFixtures(run: (fx: Fixtures) => Promise<void>): Promise<void> {
  let dir: string | null = null;
  let db: DatabaseSync | null = null;
  try {
    dir = mkdtempSync(path.join(tmpdir(), 'ornith-export-'));
    db = openDatabase(':memory:');
    const store = createConversationStore(db);
    await run({ dir, store });
  } finally {
    db?.close();
    if (dir) rmSync(dir, { recursive: true, force: true });
  }
}

/** The handler never dereferences the window beyond passing it through. */
function fakeWindow(): BrowserWindow {
  return {} as unknown as BrowserWindow;
}

function stubDialog(result: SaveDialogReturnValue): {
  showSaveDialog: ShowSaveDialog;
  calls: SaveDialogOptions[];
} {
  const calls: SaveDialogOptions[] = [];
  const showSaveDialog: ShowSaveDialog = async (_window, options) => {
    calls.push(options);
    return result;
  };
  return { showSaveDialog, calls };
}

function baseDeps(store: ConversationLookup, dialog: { showSaveDialog: ShowSaveDialog }): ExportDeps {
  return {
    store,
    getWindow: () => fakeWindow(),
    showSaveDialog: dialog.showSaveDialog,
  };
}

describe('exportConversation', () => {
  it('writes the formatted conversation to the chosen path and reports the real byte length', async () => {
    await withFixtures(async ({ dir, store }) => {
      const conv = store.create('ornith-en');
      store.rename(conv.id, 'Café notes ☕️');
      const turn = store.beginTurn(conv.id, 'Hello café', 'ornith-en');
      store.finalise(turn.assistantMessage.id, {
        content: 'Réponse: 日本語 emoji 🎉',
        thinking: '',
        status: 'complete',
      });

      const conversation = store.get(conv.id)!;
      const expectedText = formatConversation(conversation, {
        format: 'markdown',
        includeReasoning: false,
      });
      // Sanity check that this fixture actually exercises the non-ASCII case
      // the `bytes` field exists to cover — otherwise the assertion below is
      // vacuous.
      expect(Buffer.byteLength(expectedText, 'utf8')).not.toBe(expectedText.length);

      const chosenPath = path.join(dir, 'export.md');
      const dialog = stubDialog({ canceled: false, filePath: chosenPath });

      const result = await exportConversation(
        { id: conv.id, format: 'markdown', includeReasoning: false },
        baseDeps(store, dialog),
      );

      if (result.status !== 'saved') throw new Error(`expected saved, got ${result.status}`);

      // The renderer only ever learns the path main itself chose to hand
      // back — never anything it supplied or could influence.
      expect(result.path).toBe(chosenPath);

      const onDisk = readFileSync(chosenPath);
      expect(onDisk.toString('utf8')).toBe(expectedText);
      expect(onDisk.byteLength).toBe(result.bytes);
      expect(result.bytes).toBe(Buffer.byteLength(expectedText, 'utf8'));

      // suggestFilename, not a hand-rolled sanitiser, drives the dialog default.
      expect(dialog.calls[0]?.defaultPath).toBe(suggestFilename(conversation.title, 'markdown'));
    });
  });

  // `SaveDialogReturnValue.filePath` is `string`, not `string | undefined` —
  // a real dialog reports cancellation as `{ canceled: true, filePath: '' }`,
  // never by omitting the field. Production code treats `canceled` and an
  // empty `filePath` as two independent reasons to cancel
  // (`dialogResult.canceled || !dialogResult.filePath`), so each is exercised
  // in its own test rather than only ever tripping the first condition.

  it('returns cancelled and writes nothing when the dialog reports canceled', async () => {
    await withFixtures(async ({ dir, store }) => {
      const conv = store.create('ornith-en');
      const dialog = stubDialog({ canceled: true, filePath: '' });

      const result = await exportConversation(
        { id: conv.id, format: 'json', includeReasoning: false },
        baseDeps(store, dialog),
      );

      expect(result).toEqual({ status: 'cancelled' });
      expect(readdirSync(dir)).toHaveLength(0);
    });
  });

  it('returns cancelled and writes nothing when the dialog reports no chosen path', async () => {
    await withFixtures(async ({ dir, store }) => {
      const conv = store.create('ornith-en');
      // Not how a real dialog reports cancellation (that's `canceled: true`
      // above) — this exercises the `!dialogResult.filePath` half of the
      // production check independently, in case the two conditions ever
      // diverge.
      const dialog = stubDialog({ canceled: false, filePath: '' });

      const result = await exportConversation(
        { id: conv.id, format: 'json', includeReasoning: false },
        baseDeps(store, dialog),
      );

      expect(result).toEqual({ status: 'cancelled' });
      expect(readdirSync(dir)).toHaveLength(0);
    });
  });

  it('returns an error and never opens the dialog for an unknown conversation id', async () => {
    await withFixtures(async ({ dir, store }) => {
      const dialog = stubDialog({ canceled: false, filePath: path.join(dir, 'unused.md') });

      const result = await exportConversation(
        { id: 'does-not-exist', format: 'markdown', includeReasoning: false },
        baseDeps(store, dialog),
      );

      if (result.status !== 'error') throw new Error(`expected error, got ${result.status}`);
      expect(result.message.length).toBeGreaterThan(0);

      // Trust boundary: a bad id must never even get as far as offering the
      // user a place to save a file that doesn't exist.
      expect(dialog.calls).toHaveLength(0);
      expect(readdirSync(dir)).toHaveLength(0);
    });
  });

  it('rejects a malformed request without touching the store or the dialog', async () => {
    await withFixtures(async ({ dir, store }) => {
      const getSpy = vi.fn(store.get.bind(store));
      const lookup: ConversationLookup = { get: getSpy };
      const dialog = stubDialog({ canceled: false, filePath: path.join(dir, 'unused.md') });

      // Not a valid ExportRequest at all: wrong types for every field, as a
      // compromised renderer might send. The renderer is untrusted (SPEC.md
      // §11.3), so this must resolve to an error, never throw.
      const malformed = { id: 42, format: 'pdf', includeReasoning: 'yes' };

      const result = await exportConversation(malformed, baseDeps(lookup, dialog));

      if (result.status !== 'error') throw new Error(`expected error, got ${result.status}`);
      expect(getSpy).not.toHaveBeenCalled();
      expect(dialog.calls).toHaveLength(0);
    });
  });

  it('returns a safe error and does not crash when the write fails', async () => {
    await withFixtures(async ({ dir, store }) => {
      const conv = store.create('ornith-en');
      store.beginTurn(conv.id, 'hi', 'ornith-en');

      // The parent directory does not exist, so the write fails deterministically
      // (ENOENT) regardless of the user running the test.
      const chosenPath = path.join(dir, 'missing-subdir', 'export.md');
      const dialog = stubDialog({ canceled: false, filePath: chosenPath });

      const result = await exportConversation(
        { id: conv.id, format: 'markdown', includeReasoning: false },
        baseDeps(store, dialog),
      );

      if (result.status !== 'error') throw new Error(`expected error, got ${result.status}`);
      expect(result.message.length).toBeGreaterThan(0);
      // Safe to show a user: no stack trace, no raw filesystem error, and no
      // leak of the internal path beyond what the user themselves chose.
      expect(result.message).not.toMatch(/ENOENT/i);
      expect(result.message).not.toContain(dir);

      expect(readdirSync(dir)).toHaveLength(0);
    });
  });
});
