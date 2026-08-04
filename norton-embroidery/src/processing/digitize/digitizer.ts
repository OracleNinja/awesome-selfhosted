/**
 * First-pass automatic digitization.
 *
 * Regions found by the analyser are traced, classified and turned into
 * EmbroideryObjects with sensible stitch parameters. The result is a starting
 * point for the operator, not a finished design — every uncertain decision is
 * recorded on the object's `notes` so it surfaces in the editor.
 */

import {
  boundsHeight,
  boundsOf,
  boundsWidth,
  polygonArea,
  ringArea,
  type Point,
  type Polygon,
} from '../../domain/geometry';
import {
  DEFAULTS,
  defaultUnderlay,
  type EmbroideryObject,
  type StitchType,
} from '../../domain/embroidery-object';
import type { ArtworkAnalysis } from '../image/analysis';
import { MIN_FEATURE_MM, SATIN_MAX_MM } from '../image/analysis';
import { cleanMask, type Region } from '../image/segment';
import { maskToPolygons } from '../trace/contour';
import { simplifyPolygon, smoothPolygon, transformPolygon } from '../trace/simplify';
import { columnCenterline, columnFromRing, columnWidths } from '../stitches/satin';
import { nearestThread, type Thread } from '../../domain/thread';
import { mmToUnits, unitsToMm } from '../../domain/units';
import { optimizeOrder } from '../optimize/order';

export interface DigitizeOptions {
  /** Physical design width in 0.1 mm units. */
  targetWidth: number;
  /** Physical design height in 0.1 mm units. */
  targetHeight: number;
  /** Threads available for mapping artwork colours. */
  availableThreads: Thread[];
  /** Fill density in 0.1 mm units. */
  fillDensity: number;
  /** Satin density in 0.1 mm units. */
  satinDensity: number;
  /** Add underlay to generated objects. */
  underlay: boolean;
  /** Fill angle in degrees applied to the first colour; later colours rotate. */
  baseAngle: number;
  /** Discard traced shapes smaller than this area, in square 0.1 mm units. */
  minObjectArea: number;
}

export interface DigitizeResult {
  objects: EmbroideryObject[];
  /** Thread palette, indexed by EmbroideryObject.threadIndex. */
  threadPalette: Thread[];
  /** Mapping notes: artwork colour -> chosen thread. */
  colorMapping: Array<{
    artworkHex: string;
    thread: Thread;
    /** Perceptual distance between the artwork colour and the thread. */
    distance: number;
    objectCount: number;
  }>;
  warnings: string[];
}

export function defaultDigitizeOptions(
  targetWidth: number,
  targetHeight: number,
  availableThreads: Thread[],
): DigitizeOptions {
  return {
    targetWidth,
    targetHeight,
    availableThreads,
    fillDensity: DEFAULTS.fillDensity,
    satinDensity: DEFAULTS.satinDensity,
    underlay: true,
    baseAngle: 45,
    minObjectArea: mmToUnits(1) * mmToUnits(1),
  };
}

export function digitize(analysis: ArtworkAnalysis, options: DigitizeOptions): DigitizeResult {
  const warnings: string[] = [];
  const objects: EmbroideryObject[] = [];

  if (options.availableThreads.length === 0) {
    return {
      objects: [],
      threadPalette: [],
      colorMapping: [],
      warnings: ['No thread chart is loaded, so colours cannot be mapped.'],
    };
  }

  // Analysis pixels -> design units.
  const aw = analysis.analysisDimensions.width;
  const ah = analysis.analysisDimensions.height;
  const scaleX = options.targetWidth / aw;
  const scaleY = options.targetHeight / ah;
  const toDesign = (p: Point): Point => ({ x: p.x * scaleX, y: p.y * scaleY });
  const mmPerPx = (unitsToMm(scaleX) + unitsToMm(scaleY)) / 2;

  // --- thread mapping ---------------------------------------------------
  const threadPalette: Thread[] = [];
  const threadIndexByColor = new Map<number, number>();
  const mappingStats = new Map<number, { artworkHex: string; thread: Thread; distance: number; objectCount: number }>();

  const threadIndexFor = (colorIndex: number): number => {
    const existing = threadIndexByColor.get(colorIndex);
    if (existing !== undefined) return existing;
    const c = analysis.detectedColors[colorIndex];
    const thread = nearestThread(c, options.availableThreads);
    // Reuse the palette slot when two artwork colours map to the same thread:
    // sewing the same cone twice would be a pointless colour change.
    let index = threadPalette.findIndex((t) => t.id === thread.id);
    if (index < 0) {
      index = threadPalette.length;
      threadPalette.push(thread);
    }
    threadIndexByColor.set(colorIndex, index);
    mappingStats.set(colorIndex, {
      artworkHex: c.hex,
      thread,
      distance: colorDistanceOf(c, thread),
      objectCount: 0,
    });
    return index;
  };

  // --- regions -> objects ----------------------------------------------
  let nextId = 0;
  const makeId = (): string => `obj-${++nextId}`;

  const foregroundIds = new Set(analysis.foregroundRegions.map((r) => r.id));
  const regions = analysis.regions.filter((r) => foregroundIds.has(r.id));

  for (const region of regions) {
    const summary = analysis.foregroundRegions.find((r) => r.id === region.id)!;
    // Speckle removal erodes before it dilates, which annihilates a region only
    // a pixel or two wide. Those are exactly the thin strokes that should
    // become running stitches, so fall back to the untouched mask rather than
    // dropping the shape.
    const cleaned = cleanMask(region.mask, region.width, region.height, 1);
    let rawPolys = maskToPolygons(cleaned, region.width, region.height);
    if (rawPolys.length === 0) {
      rawPolys = maskToPolygons(region.mask, region.width, region.height);
    }
    if (rawPolys.length === 0) {
      warnings.push(
        `A ${region.pixelCount}-pixel region of colour ${analysis.detectedColors[region.colorIndex]?.hex ?? '?'} could not be traced and was skipped.`,
      );
      continue;
    }

    const threadIndex = threadIndexFor(region.colorIndex);
    const stat = mappingStats.get(region.colorIndex);

    for (const raw of rawPolys) {
      // Simplify in pixel space (tolerance in pixels), smooth away the
      // staircase left by the bitmap trace, then simplify again. The second
      // pass collapses the points Chaikin adds along straight edges, so a
      // rectangle stays a rectangle instead of relaxing into a blob while
      // genuinely curved outlines stay smooth.
      const simplified = simplifyPolygon(raw, 0.8);
      const smoothed = smoothPolygon(simplified, 2);
      const cleaned = simplifyPolygon(smoothed, 0.35);
      const poly = transformPolygon(cleaned, toDesign);
      if (poly.outer.length < 3) continue;

      const area = polygonArea(poly);
      if (area < options.minObjectArea) continue;

      const bounds = boundsOf(poly.outer);
      const widthMm = unitsToMm(Math.min(boundsWidth(bounds), boundsHeight(bounds)));
      const type = classify(region, summary.suggestedStitchType, poly, mmPerPx);

      const obj = buildObject({
        id: makeId(),
        name: `${type} · ${analysis.detectedColors[region.colorIndex]?.hex ?? 'colour'}`,
        type,
        poly,
        threadIndex,
        options,
        colorIndex: region.colorIndex,
      });
      if (!obj) continue;

      if (summary.tooSmall) {
        obj.notes.push(
          `Narrowest feature is about ${(summary.widthPx * mmPerPx).toFixed(2)} mm, below the ${MIN_FEATURE_MM} mm minimum. Check this area before sewing.`,
        );
      }
      if (type === 'satin') {
        const column = columnFromRing(poly.outer);
        if (column) {
          const widths = columnWidths(column).map(unitsToMm);
          const maxWidth = Math.max(...widths);
          if (maxWidth > SATIN_MAX_MM) {
            obj.notes.push(
              `Column reaches ${maxWidth.toFixed(1)} mm wide. Satin stitches longer than about ${SATIN_MAX_MM} mm snag; consider changing this object to a fill.`,
            );
          }
        }
      }
      if (widthMm < MIN_FEATURE_MM) {
        obj.notes.push('Object is thinner than 1 mm and may not reproduce.');
      }

      objects.push(obj);
      if (stat) stat.objectCount++;
    }
  }

  if (objects.length === 0) {
    warnings.push('Automatic digitizing produced no objects. The artwork may be blank, or every shape may be below the minimum size.');
  }

  // Rotate the fill angle per thread so adjacent colour areas do not all run
  // the same way, which reads as flat.
  const angleByThread = new Map<number, number>();
  threadPalette.forEach((_, i) => angleByThread.set(i, (options.baseAngle + i * 30) % 180));
  for (const obj of objects) {
    if (obj.type === 'fill') obj.angle = angleByThread.get(obj.threadIndex) ?? options.baseAngle;
  }

  const ordered = optimizeOrder(objects);

  return {
    objects: ordered.objects,
    threadPalette,
    colorMapping: [...mappingStats.values()],
    warnings,
  };
}

function classify(
  region: Region,
  suggested: StitchType,
  poly: Polygon,
  mmPerPx: number,
): StitchType {
  const widthMm = region.maxHalfWidth * 2 * mmPerPx;
  const typicalMm = region.medianHalfWidth * 2 * mmPerPx;

  if (widthMm < MIN_FEATURE_MM * 1.6) return 'running';
  // A shape with holes cannot be a satin column: the rails would cross them.
  if (poly.holes.length > 0) return 'fill';
  if (typicalMm <= SATIN_MAX_MM && region.elongation >= 1.8 && ringArea(poly.outer) > 0) return 'satin';
  return suggested === 'satin' ? 'fill' : suggested;
}

function buildObject(args: {
  id: string;
  name: string;
  type: StitchType;
  poly: Polygon;
  threadIndex: number;
  options: DigitizeOptions;
  colorIndex: number;
}): EmbroideryObject | null {
  const { id, name, type, poly, threadIndex, options } = args;

  const base = {
    id,
    name,
    threadIndex,
    order: 0,
    forceTrimBefore: false,
    visible: true,
    notes: [] as string[],
    autoGenerated: true,
  };

  if (type === 'satin') {
    const column = columnFromRing(poly.outer);
    if (!column) {
      // Fall back to a fill rather than dropping the shape.
      return {
        ...base,
        type: 'fill',
        geometry: { kind: 'polygon', polygon: poly },
        angle: options.baseAngle,
        density: options.fillDensity,
        stitchLength: DEFAULTS.fillStitchLength,
        underlay: options.underlay ? defaultUnderlay('edge-run') : defaultUnderlay('none'),
        notes: ['Could not derive a satin column from this shape; stitched as a fill.'],
      };
    }
    return {
      ...base,
      type: 'satin',
      geometry: { kind: 'column', left: column.left, right: column.right },
      angle: 0,
      density: options.satinDensity,
      stitchLength: DEFAULTS.fillStitchLength,
      underlay: options.underlay ? defaultUnderlay('center-run') : defaultUnderlay('none'),
    };
  }

  if (type === 'running') {
    const column = columnFromRing(poly.outer);
    const center = column ? columnCenterline(column) : poly.outer;
    if (center.length < 2) return null;
    return {
      ...base,
      type: 'running',
      geometry: { kind: 'path', points: center, closed: false },
      angle: 0,
      density: options.satinDensity,
      stitchLength: DEFAULTS.runningStitchLength,
      underlay: defaultUnderlay('none'),
      notes: ['Too thin to fill; stitched as a single running-stitch line.'],
    };
  }

  return {
    ...base,
    type: 'fill',
    geometry: { kind: 'polygon', polygon: poly },
    angle: options.baseAngle,
    density: options.fillDensity,
    stitchLength: DEFAULTS.fillStitchLength,
    underlay: options.underlay ? defaultUnderlay('edge-run') : defaultUnderlay('none'),
  };
}

function colorDistanceOf(c: { r: number; g: number; b: number }, t: Thread): number {
  const rMean = (c.r + t.r) / 2;
  const dr = c.r - t.r;
  const dg = c.g - t.g;
  const db = c.b - t.b;
  return Math.sqrt(((512 + rMean) * dr * dr) / 256 + 4 * dg * dg + ((767 - rMean) * db * db) / 256);
}
