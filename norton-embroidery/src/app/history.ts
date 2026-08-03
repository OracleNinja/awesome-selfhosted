/**
 * Undo/redo.
 *
 * The editor keeps a stack of whole design snapshots. Designs are plain data
 * and a first-pass logo is a few thousand stitches, so snapshotting is simpler
 * and far less error-prone than command inversion.
 */

import type { EmbroideryDesign } from '../domain/design';

export interface HistoryEntry {
  design: EmbroideryDesign;
  /** What produced this state, shown in the UI. */
  label: string;
}

export interface History {
  past: HistoryEntry[];
  present: HistoryEntry;
  future: HistoryEntry[];
  limit: number;
}

export function createHistory(design: EmbroideryDesign, label = 'Initial state', limit = 50): History {
  return { past: [], present: { design, label }, future: [], limit };
}

export function push(history: History, design: EmbroideryDesign, label: string): History {
  const past = [...history.past, history.present];
  return {
    ...history,
    past: past.length > history.limit ? past.slice(past.length - history.limit) : past,
    present: { design, label },
    future: [],
  };
}

export function canUndo(history: History): boolean {
  return history.past.length > 0;
}

export function canRedo(history: History): boolean {
  return history.future.length > 0;
}

export function undo(history: History): History {
  if (!canUndo(history)) return history;
  const previous = history.past[history.past.length - 1];
  return {
    ...history,
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  };
}

export function redo(history: History): History {
  if (!canRedo(history)) return history;
  const [next, ...rest] = history.future;
  return {
    ...history,
    past: [...history.past, history.present],
    present: next,
    future: rest,
  };
}

/** Label of the change that undo would reverse. */
export function undoLabel(history: History): string | null {
  return canUndo(history) ? history.present.label : null;
}

export function redoLabel(history: History): string | null {
  return canRedo(history) ? history.future[0].label : null;
}
