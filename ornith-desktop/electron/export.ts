/**
 * Conversation export — the privileged half of the export feature.
 *
 * SPEC.md §3.1 puts all filesystem access in main: this module resolves a
 * conversation from the store, turns it into text with the pure formatter in
 * `shared/exportFormat.ts`, asks the user where to save it via a native save
 * dialog, and writes the result. SPEC.md §11.3 treats every renderer→main
 * payload as untrusted, so the request is validated field by field before
 * anything else happens.
 *
 * The trust boundary this exists to protect: the renderer sends only an
 * `id`, `format`, and `includeReasoning`, and gets back an `ExportResult`. It
 * never supplies, influences, or learns a filesystem path other than the one
 * this module hands back inside that result — the save path comes from the
 * user's own choice in the native dialog, not from the renderer.
 *
 * Testability seam: everything here is plain TypeScript over `node:fs` and a
 * store lookup, both real (and exercised for real) in tests. Only
 * `showSaveDialog` — the one part that genuinely needs a live Electron
 * window — is an injected dependency, so the write path can be exercised
 * against a real temp directory without launching Electron.
 */
import { writeFile } from 'node:fs/promises';
import type { BrowserWindow, SaveDialogOptions, SaveDialogReturnValue } from 'electron';
import { formatConversation, suggestFilename } from '../shared/exportFormat';
import type { ExportFormat, ExportRequest, ExportResult } from '../shared/types';
import type { ConversationStore } from './store/conversations';
import { log } from './log';

/** All this module needs from the store — narrow, so tests can supply a fake. */
export type ConversationLookup = Pick<ConversationStore, 'get'>;

/** Matches `dialog.showSaveDialog`'s shape closely enough to inject a fake in tests. */
export type ShowSaveDialog = (
  window: BrowserWindow,
  options: SaveDialogOptions,
) => Promise<SaveDialogReturnValue>;

export interface ExportDeps {
  store: ConversationLookup;
  /** Resolves the window to anchor the dialog to; null means none is available. */
  getWindow: () => BrowserWindow | null;
  showSaveDialog: ShowSaveDialog;
}

const FORMAT_FILTERS: Record<ExportFormat, NonNullable<SaveDialogOptions['filters']>> = {
  markdown: [{ name: 'Markdown', extensions: ['md'] }],
  json: [{ name: 'JSON', extensions: ['json'] }],
};

function isExportFormat(value: unknown): value is ExportFormat {
  return value === 'markdown' || value === 'json';
}

/**
 * The renderer is untrusted (SPEC.md §11.3), so every field is checked
 * before anything downstream relies on it — mirroring the defensive
 * `typeof` checks `conv:confirm-delete` and friends already use in main.ts.
 */
function isValidRequest(value: unknown): value is ExportRequest {
  if (typeof value !== 'object' || value === null) return false;
  const req = value as Record<string, unknown>;
  return (
    typeof req.id === 'string' &&
    req.id.length > 0 &&
    isExportFormat(req.format) &&
    typeof req.includeReasoning === 'boolean'
  );
}

/**
 * Opens a native save dialog, formats and writes the requested conversation,
 * and reports what happened. Never throws — every failure mode, including a
 * malformed request, an unknown conversation, and a write failure, resolves
 * to an `ExportResult` so it can cross IPC safely.
 */
export async function exportConversation(
  request: unknown,
  deps: ExportDeps,
): Promise<ExportResult> {
  if (!isValidRequest(request)) {
    log.warn('export.invalid_request');
    return { status: 'error', message: 'That export request was invalid.' };
  }

  let conversation;
  try {
    conversation = deps.store.get(request.id);
  } catch (err) {
    log.error('export.lookup_failed', { detail: String(err) });
    return { status: 'error', message: 'Could not read that conversation.' };
  }

  if (!conversation) {
    log.warn('export.unknown_conversation');
    return { status: 'error', message: 'That conversation could not be found.' };
  }

  const window = deps.getWindow();
  if (!window) {
    log.error('export.no_window');
    return { status: 'error', message: 'Export is unavailable right now.' };
  }

  let text: string;
  try {
    text = formatConversation(conversation, {
      format: request.format,
      includeReasoning: request.includeReasoning,
    });
  } catch (err) {
    log.error('export.format_failed', { detail: String(err) });
    return { status: 'error', message: 'Could not prepare the export.' };
  }

  let dialogResult: SaveDialogReturnValue;
  try {
    dialogResult = await deps.showSaveDialog(window, {
      defaultPath: suggestFilename(conversation.title, request.format),
      filters: FORMAT_FILTERS[request.format],
    });
  } catch (err) {
    log.error('export.dialog_failed', { detail: String(err) });
    return { status: 'error', message: 'Could not open the save dialog.' };
  }

  // Cancelling is a normal outcome, never an error: nothing is written and
  // nothing alarming is logged.
  if (dialogResult.canceled || !dialogResult.filePath) {
    return { status: 'cancelled' };
  }

  // The formatter emits UTF-8; measure the real encoded byte length rather
  // than `text.length`, which undercounts for any non-ASCII conversation.
  const bytes = Buffer.from(text, 'utf8');
  try {
    await writeFile(dialogResult.filePath, bytes);
  } catch (err) {
    log.error('export.write_failed', { detail: String(err) });
    return {
      status: 'error',
      message: 'Could not save the file. Check that the location is writable and try again.',
    };
  }

  log.info('export.saved', { format: request.format, bytes: bytes.byteLength });
  return { status: 'saved', path: dialogResult.filePath, bytes: bytes.byteLength };
}
