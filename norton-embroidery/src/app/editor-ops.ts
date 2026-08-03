/**
 * Editing operations.
 *
 * Each operation transforms objects and then regenerates stitches, so the
 * preview, statistics and export never drift from what the operator edited.
 */

import { boundsCenter, boundsOf, rotatePoint, type Point } from '../domain/geometry';
import type { EmbroideryDesign } from '../domain/design';
import type { MachineProfile } from '../domain/machine';
import {
  cloneObject,
  mapGeometry,
  objectPoints,
  type EmbroideryObject,
  type StitchType,
} from '../domain/embroidery-object';
import type { Thread } from '../domain/thread';
import { moveInOrder } from '../processing/optimize/order';
import { columnFromRing } from '../processing/stitches/satin';
import { regenerate } from './pipeline';

type Op = (design: EmbroideryDesign, machine: MachineProfile) => EmbroideryDesign;

function withObjects(
  design: EmbroideryDesign,
  objects: EmbroideryObject[],
  machine: MachineProfile,
): EmbroideryDesign {
  return regenerate({ ...design, objects }, machine);
}

function transformObject(obj: EmbroideryObject, fn: (p: Point) => Point): EmbroideryObject {
  return { ...obj, geometry: mapGeometry(obj.geometry, fn) };
}

export const moveObject =
  (objectId: string, dx: number, dy: number): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.map((o) =>
        o.id === objectId ? transformObject(o, (p) => ({ x: p.x + dx, y: p.y + dy })) : o,
      ),
      machine,
    );

export const scaleObject =
  (objectId: string, sx: number, sy: number): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.map((o) => {
        if (o.id !== objectId) return o;
        const origin = boundsCenter(boundsOf(objectPoints(o)));
        return transformObject(o, (p) => ({
          x: origin.x + (p.x - origin.x) * sx,
          y: origin.y + (p.y - origin.y) * sy,
        }));
      }),
      machine,
    );

export const rotateObject =
  (objectId: string, degrees: number): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.map((o) => {
        if (o.id !== objectId) return o;
        const origin = boundsCenter(boundsOf(objectPoints(o)));
        const rad = (degrees * Math.PI) / 180;
        return transformObject(o, (p) => rotatePoint(p, rad, origin));
      }),
      machine,
    );

export const deleteObject =
  (objectId: string): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.filter((o) => o.id !== objectId).map((o, i) => ({ ...o, order: i })),
      machine,
    );

export const duplicateObject =
  (objectId: string): Op =>
  (design, machine) => {
    const source = design.objects.find((o) => o.id === objectId);
    if (!source) return design;
    const copy = cloneObject(source, `${source.id}-copy-${Date.now().toString(36)}`);
    // Offset the copy slightly so it is visible and selectable.
    const offset = transformObject(copy, (p) => ({ x: p.x + 20, y: p.y + 20 }));
    const objects = [...design.objects, { ...offset, order: design.objects.length }];
    return withObjects(design, objects, machine);
  };

export const setObjectProperty =
  <K extends keyof EmbroideryObject>(objectId: string, key: K, value: EmbroideryObject[K]): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.map((o) => (o.id === objectId ? { ...o, [key]: value } : o)),
      machine,
    );

/**
 * Change an object's stitch type, converting geometry where the new type needs
 * a different representation. Returns the design unchanged when the conversion
 * is not possible, rather than producing a broken object.
 */
export const setStitchType =
  (objectId: string, type: StitchType): Op =>
  (design, machine) => {
    const objects = design.objects.map((o) => {
      if (o.id !== objectId) return o;
      if (o.type === type) return o;

      if (type === 'satin' && o.geometry.kind === 'polygon') {
        const column = columnFromRing(o.geometry.polygon.outer);
        if (!column) return o;
        return { ...o, type, geometry: { kind: 'column' as const, left: column.left, right: column.right } };
      }
      if ((type === 'fill' || type === 'outline') && o.geometry.kind === 'column') {
        // Rebuild a closed ring from the two rails.
        const ring = [...o.geometry.left, ...[...o.geometry.right].reverse()];
        return { ...o, type, geometry: { kind: 'polygon' as const, polygon: { outer: ring, holes: [] } } };
      }
      return { ...o, type };
    });
    return withObjects(design, objects, machine);
  };

export const reorderObject =
  (objectId: string, newIndex: number): Op =>
  (design, machine) =>
    withObjects(design, moveInOrder(design.objects, objectId, newIndex), machine);

/**
 * Change the start point of a closed object by rotating its point list, which
 * moves where the needle enters without changing the shape.
 */
export const setStartPoint =
  (objectId: string, pointIndex: number): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.map((o) => {
        if (o.id !== objectId) return o;
        if (o.geometry.kind === 'polygon') {
          const ring = o.geometry.polygon.outer;
          const i = ((pointIndex % ring.length) + ring.length) % ring.length;
          return {
            ...o,
            geometry: {
              kind: 'polygon',
              polygon: { outer: [...ring.slice(i), ...ring.slice(0, i)], holes: o.geometry.polygon.holes },
            },
          };
        }
        if (o.geometry.kind === 'path' && o.geometry.closed) {
          const pts = o.geometry.points;
          const i = ((pointIndex % pts.length) + pts.length) % pts.length;
          return { ...o, geometry: { kind: 'path', points: [...pts.slice(i), ...pts.slice(0, i)], closed: true } };
        }
        return o;
      }),
      machine,
    );

/** Reverse an open path, which swaps its start and end points. */
export const reversePath =
  (objectId: string): Op =>
  (design, machine) =>
    withObjects(
      design,
      design.objects.map((o) => {
        if (o.id !== objectId || o.geometry.kind !== 'path') return o;
        return { ...o, geometry: { ...o.geometry, points: [...o.geometry.points].reverse() } };
      }),
      machine,
    );

/** Replace a palette slot with a different thread. */
export function replaceThread(
  design: EmbroideryDesign,
  threadIndex: number,
  thread: Thread,
): EmbroideryDesign {
  const palette = design.threadPalette.map((t, i) => (i === threadIndex ? thread : t));
  return { ...design, threadPalette: palette };
}

/** Move every object so the design is centred in the hoop field. */
export function centerDesign(
  design: EmbroideryDesign,
  machine: MachineProfile,
  hoopWidth: number,
  hoopHeight: number,
): EmbroideryDesign {
  const all = design.objects.flatMap(objectPoints);
  if (all.length === 0) return design;
  const b = boundsOf(all);
  const dx = hoopWidth / 2 - (b.minX + b.maxX) / 2;
  const dy = hoopHeight / 2 - (b.minY + b.maxY) / 2;
  return withObjects(
    design,
    design.objects.map((o) => transformObject(o, (p) => ({ x: p.x + dx, y: p.y + dy }))),
    machine,
  );
}

/** Scale the whole design about its own centre. */
export function scaleDesign(
  design: EmbroideryDesign,
  machine: MachineProfile,
  factor: number,
): EmbroideryDesign {
  const all = design.objects.flatMap(objectPoints);
  if (all.length === 0) return design;
  const origin = boundsCenter(boundsOf(all));
  return withObjects(
    design,
    design.objects.map((o) =>
      transformObject(o, (p) => ({
        x: origin.x + (p.x - origin.x) * factor,
        y: origin.y + (p.y - origin.y) * factor,
      })),
    ),
    machine,
  );
}
