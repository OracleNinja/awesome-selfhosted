/**
 * Versioned project format: `.norton-embroidery-project`.
 *
 * The file is UTF-8 JSON. It carries the original uploaded artwork verbatim as
 * a base64 data URL, so re-opening a project can always go back to the
 * customer's file — the original is never overwritten by processing.
 *
 * `formatVersion` is checked on load; older files are migrated forward by
 * `migrate()` and newer files are refused with a clear message rather than
 * being partially read.
 */

import type { EmbroideryDesign } from '../../domain/design';
import type { ArtworkAnalysis } from '../../processing/image/analysis';
import type { EmbroideryObject } from '../../domain/embroidery-object';
import type { Thread } from '../../domain/thread';
import type { Stitch } from '../../domain/stitch';
import type { ValidationReport } from '../../domain/validation';

export const PROJECT_FORMAT_VERSION = 3;
export const PROJECT_FILE_EXTENSION = '.norton-embroidery-project';

export interface StoredArtwork {
  fileName: string;
  mimeType: string;
  /** Original bytes, base64 (no data: prefix). Never modified after upload. */
  base64: string;
  width: number;
  height: number;
  byteLength: number;
}

/**
 * The analysis is stored without its heavy pixel buffers; those are recomputed
 * from the original artwork when the project is opened.
 */
export type SerializableAnalysis = Omit<ArtworkAnalysis, 'quantized' | 'regions'>;

export interface ProjectFile {
  formatVersion: number;
  application: 'norton-embroidery';
  savedAt: string;
  project: {
    id: string;
    name: string;
    customer?: string;
    createdAt: string;
    modifiedAt: string;
    notes?: string;
  };
  machineId: string;
  hoopId: string;
  canvas: { width: number; height: number };
  artwork: StoredArtwork | null;
  analysis: SerializableAnalysis | null;
  threadPalette: Thread[];
  objects: EmbroideryObject[];
  stitches: Stitch[];
  /**
   * Palette index per colour block, in sew order. Added in format 3; older
   * files are migrated by assuming the one-to-one mapping that was implicit
   * before a design could return to a colour.
   */
  colorSequence: number[] | null;
  validation: ValidationReport | null;
}

export interface ProjectState {
  id: string;
  design: EmbroideryDesign;
  artwork: StoredArtwork | null;
  analysis: SerializableAnalysis | null;
}

export function serializeProject(state: ProjectState): ProjectFile {
  const { design } = state;
  return {
    formatVersion: PROJECT_FORMAT_VERSION,
    application: 'norton-embroidery',
    savedAt: new Date().toISOString(),
    project: {
      id: state.id,
      name: design.metadata.name,
      customer: design.metadata.customer,
      createdAt: design.metadata.createdAt,
      modifiedAt: design.metadata.modifiedAt,
      notes: design.metadata.notes,
    },
    machineId: design.canvas.machineId,
    hoopId: design.canvas.hoopId,
    canvas: { width: design.canvas.width, height: design.canvas.height },
    artwork: state.artwork,
    analysis: state.analysis,
    threadPalette: design.threadPalette,
    objects: design.objects,
    stitches: design.stitches,
    colorSequence: design.colorSequence,
    validation: design.validation,
  };
}

export class ProjectFormatError extends Error {}

export function deserializeProject(raw: unknown): ProjectState {
  if (typeof raw !== 'object' || raw === null) {
    throw new ProjectFormatError('The project file is not valid JSON object data.');
  }
  const file = raw as Partial<ProjectFile>;

  if (file.application !== 'norton-embroidery') {
    throw new ProjectFormatError(
      `This file was not written by Norton Embroidery (it declares application "${String(file.application)}").`,
    );
  }

  const version = typeof file.formatVersion === 'number' ? file.formatVersion : 0;
  if (version > PROJECT_FORMAT_VERSION) {
    throw new ProjectFormatError(
      `The project was saved by a newer version of the application (format ${version}; this build understands up to ${PROJECT_FORMAT_VERSION}). Update the application to open it.`,
    );
  }

  const migrated = migrate(file, version);

  return {
    id: migrated.project.id,
    artwork: migrated.artwork,
    analysis: migrated.analysis,
    design: {
      metadata: {
        name: migrated.project.name,
        customer: migrated.project.customer,
        createdAt: migrated.project.createdAt,
        modifiedAt: migrated.project.modifiedAt,
        notes: migrated.project.notes,
      },
      canvas: {
        width: migrated.canvas.width,
        height: migrated.canvas.height,
        machineId: migrated.machineId,
        hoopId: migrated.hoopId,
      },
      threadPalette: migrated.threadPalette,
      objects: migrated.objects,
      stitches: migrated.stitches,
      colorSequence: migrated.colorSequence,
      validation: migrated.validation,
    },
  };
}

/** Bring an older project file forward to the current format. */
function migrate(file: Partial<ProjectFile>, version: number): ProjectFile {
  const out: ProjectFile = {
    formatVersion: PROJECT_FORMAT_VERSION,
    application: 'norton-embroidery',
    savedAt: file.savedAt ?? new Date().toISOString(),
    project: {
      id: file.project?.id ?? crypto.randomUUID(),
      name: file.project?.name ?? 'Untitled project',
      customer: file.project?.customer,
      createdAt: file.project?.createdAt ?? new Date().toISOString(),
      modifiedAt: file.project?.modifiedAt ?? new Date().toISOString(),
      notes: file.project?.notes,
    },
    machineId: file.machineId ?? 'brother-se700',
    hoopId: file.hoopId ?? 'se700-5x7',
    canvas: file.canvas ?? { width: 1000, height: 1000 },
    artwork: file.artwork ?? null,
    analysis: file.analysis ?? null,
    threadPalette: file.threadPalette ?? [],
    objects: file.objects ?? [],
    stitches: file.stitches ?? [],
    colorSequence: file.colorSequence ?? null,
    validation: file.validation ?? null,
  };

  if (version < 2) {
    // Format 1 stored underlay as a bare string; format 2 uses a settings object.
    out.objects = out.objects.map((obj) => {
      const raw = obj as unknown as { underlay: unknown };
      if (typeof raw.underlay === 'string') {
        return {
          ...obj,
          underlay: {
            type: raw.underlay as never,
            inset: 8,
            spacing: 25,
            stitchLength: 25,
          },
        };
      }
      return obj;
    });
  }

  if (version < 3 && out.colorSequence === null) {
    // Before format 3 the colour sequence was implicit: block n used palette
    // entry n. That was only ever true for designs where no colour repeats,
    // which is exactly what those files contained.
    const blockCount = out.stitches.filter((s) => s.command === 'COLOR_CHANGE').length + 1;
    out.colorSequence =
      out.stitches.length === 0
        ? []
        : Array.from({ length: blockCount }, (_, i) => Math.min(i, Math.max(0, out.threadPalette.length - 1)));
  }

  return out;
}

export function projectToJson(state: ProjectState): string {
  return JSON.stringify(serializeProject(state), null, 2);
}

export function projectFromJson(json: string): ProjectState {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch (err) {
    throw new ProjectFormatError(
      `The project file is not readable JSON: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return deserializeProject(parsed);
}

export function projectFileName(name: string): string {
  const cleaned = name.replace(/[^a-zA-Z0-9-_ ]/g, '').trim().replace(/\s+/g, '-');
  return `${cleaned || 'project'}${PROJECT_FILE_EXTENSION}`;
}
