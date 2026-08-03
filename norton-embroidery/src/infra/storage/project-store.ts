/**
 * Project persistence in the browser (IndexedDB).
 *
 * Projects are stored locally so work survives a reload or a closed tab. The
 * same `ProjectFile` structure is used for the downloadable
 * `.norton-embroidery-project` file, so a project saved here and one exported
 * to disk are interchangeable.
 */

import {
  deserializeProject,
  serializeProject,
  type ProjectFile,
  type ProjectState,
} from '../project/project-format';

const DB_NAME = 'norton-embroidery';
const DB_VERSION = 1;
const STORE = 'projects';

export interface ProjectSummary {
  id: string;
  name: string;
  customer?: string;
  modifiedAt: string;
  stitchCount: number;
  colorCount: number;
  hasArtwork: boolean;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'project.id' });
        store.createIndex('modifiedAt', 'project.modifiedAt');
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(new Error(`Could not open local project storage: ${request.error?.message ?? 'unknown error'}`));
  });
}

function tx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const transaction = db.transaction(STORE, mode);
        const request = fn(transaction.objectStore(STORE));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () =>
          reject(new Error(`Project storage failed: ${request.error?.message ?? 'unknown error'}`));
        transaction.oncomplete = () => db.close();
      }),
  );
}

export async function saveProject(state: ProjectState): Promise<void> {
  const file = serializeProject(state);
  await tx('readwrite', (store) => store.put(file));
}

export async function loadProject(id: string): Promise<ProjectState | null> {
  const file = await tx<ProjectFile | undefined>('readonly', (store) => store.get(id));
  return file ? deserializeProject(file) : null;
}

export async function deleteProject(id: string): Promise<void> {
  await tx('readwrite', (store) => store.delete(id));
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const all = await tx<ProjectFile[]>('readonly', (store) => store.getAll());
  return all
    .map((file) => ({
      id: file.project.id,
      name: file.project.name,
      customer: file.project.customer,
      modifiedAt: file.project.modifiedAt,
      stitchCount: file.stitches.filter((s) => s.command === 'STITCH').length,
      colorCount: file.threadPalette.length,
      hasArtwork: file.artwork !== null,
    }))
    .sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt));
}

/** Copy a project under a new id and name. */
export async function duplicateProject(id: string, newName: string): Promise<ProjectState | null> {
  const existing = await loadProject(id);
  if (!existing) return null;
  const copy: ProjectState = {
    ...existing,
    id: crypto.randomUUID(),
    design: {
      ...existing.design,
      metadata: {
        ...existing.design.metadata,
        name: newName,
        createdAt: new Date().toISOString(),
        modifiedAt: new Date().toISOString(),
      },
    },
  };
  await saveProject(copy);
  return copy;
}

export function isStorageAvailable(): boolean {
  return typeof indexedDB !== 'undefined';
}
