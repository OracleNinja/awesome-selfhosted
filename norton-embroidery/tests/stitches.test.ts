import { describe, expect, it } from 'vitest';
import { distance, polygonArea, type Point, type Polygon } from '../src/domain/geometry';
import { mmToUnits, unitsToMm } from '../src/domain/units';
import { generateFill, DEFAULT_FILL_OPTIONS } from '../src/processing/stitches/fill';
import { columnFromRing, columnWidths, generateSatin } from '../src/processing/stitches/satin';
import { beanStitch, runningStitch } from '../src/processing/stitches/running';
import { assembleStitches, generateObjectStitches } from '../src/processing/stitches/generate';
import { optimizeOrder, moveInOrder } from '../src/processing/optimize/order';
import { StitchCommand, colorBlocks, countTrims } from '../src/domain/stitch';
import { defaultUnderlay, type EmbroideryObject } from '../src/domain/embroidery-object';

const square = (x: number, y: number, size: number): Polygon => ({
  outer: [
    { x, y },
    { x: x + size, y },
    { x: x + size, y: y + size },
    { x, y: y + size },
  ],
  holes: [],
});

const squareWithHole = (size: number, hole: number): Polygon => {
  const inset = (size - hole) / 2;
  return {
    outer: [
      { x: 0, y: 0 },
      { x: size, y: 0 },
      { x: size, y: size },
      { x: 0, y: size },
    ],
    // Holes wind the opposite way to the outer ring.
    holes: [
      [
        { x: inset, y: inset },
        { x: inset, y: inset + hole },
        { x: inset + hole, y: inset + hole },
        { x: inset + hole, y: inset },
      ],
    ],
  };
};

const maxSegment = (points: Point[]): number => {
  let max = 0;
  for (let i = 1; i < points.length; i++) max = Math.max(max, distance(points[i - 1], points[i]));
  return max;
};

describe('fill stitch generation', () => {
  it('produces stitches that cover the shape at the requested density', () => {
    const poly = square(0, 0, mmToUnits(30));
    const runs = generateFill(poly, { ...DEFAULT_FILL_OPTIONS, angle: 0, density: mmToUnits(0.4) });
    expect(runs.length).toBeGreaterThan(0);

    const points = runs.flat();
    expect(points.length).toBeGreaterThan(100);

    // Rows should span roughly the full 30 mm width.
    const xs = points.map((p) => p.x);
    expect(unitsToMm(Math.max(...xs) - Math.min(...xs))).toBeGreaterThan(29);

    // Number of rows should match 30 mm / 0.4 mm within a row or two.
    const distinctRows = new Set(points.map((p) => Math.round(p.y))).size;
    expect(distinctRows).toBeGreaterThan(60);
    expect(distinctRows).toBeLessThan(85);
  });

  it('honours the fill angle', () => {
    const poly = square(0, 0, mmToUnits(20));
    const horizontal = generateFill(poly, { ...DEFAULT_FILL_OPTIONS, angle: 0 }).flat();
    const vertical = generateFill(poly, { ...DEFAULT_FILL_OPTIONS, angle: 90 }).flat();

    // A horizontal fill has one row per y, so distinct y values greatly
    // outnumber distinct x values. A vertical fill is the mirror image.
    const distinctYh = new Set(horizontal.map((p) => Math.round(p.y))).size;
    const distinctXh = new Set(horizontal.map((p) => Math.round(p.x))).size;
    const distinctYv = new Set(vertical.map((p) => Math.round(p.y))).size;
    const distinctXv = new Set(vertical.map((p) => Math.round(p.x))).size;

    expect(distinctYh).toBeGreaterThan(distinctXh);
    expect(distinctXv).toBeGreaterThan(distinctYv);
    // ~20 mm at 0.4 mm spacing is about 50 rows either way.
    expect(distinctYh).toBeGreaterThan(40);
    expect(distinctXv).toBeGreaterThan(40);
  });

  it('never stitches through a hole', () => {
    const size = mmToUnits(30);
    const holeSize = mmToUnits(12);
    const poly = squareWithHole(size, holeSize);
    const runs = generateFill(poly, { ...DEFAULT_FILL_OPTIONS, angle: 0, density: mmToUnits(0.5) });

    const inset = (size - holeSize) / 2;
    const holeMinX = inset;
    const holeMaxX = inset + holeSize;

    let insideHole = 0;
    for (const run of runs) {
      for (let i = 1; i < run.length; i++) {
        // Sample along each stitch, not just its endpoints.
        for (let t = 0; t <= 1; t += 0.25) {
          const x = run[i - 1].x + (run[i].x - run[i - 1].x) * t;
          const y = run[i - 1].y + (run[i].y - run[i - 1].y) * t;
          const inX = x > holeMinX + 1 && x < holeMaxX - 1;
          const inY = y > holeMinX + 1 && y < holeMaxX - 1;
          if (inX && inY) insideHole++;
        }
      }
    }
    expect(insideHole).toBe(0);
  });

  it('returns nothing for a shape thinner than one row', () => {
    const sliver: Polygon = {
      outer: [
        { x: 0, y: 0 },
        { x: mmToUnits(20), y: 0 },
        { x: mmToUnits(20), y: 1 },
        { x: 0, y: 1 },
      ],
      holes: [],
    };
    expect(generateFill(sliver, { ...DEFAULT_FILL_OPTIONS, density: mmToUnits(0.4) })).toEqual([]);
  });

  it('keeps every stitch within the machine stitch length', () => {
    const poly = square(0, 0, mmToUnits(40));
    const runs = generateFill(poly, { ...DEFAULT_FILL_OPTIONS, stitchLength: mmToUnits(3.5) });
    for (const run of runs) {
      // Row-to-row moves are also stitches, so allow the diagonal of one step.
      expect(maxSegment(run)).toBeLessThanOrEqual(mmToUnits(12.7));
    }
  });
});

describe('satin stitch generation', () => {
  const bar = {
    left: [
      { x: 0, y: 0 },
      { x: mmToUnits(40), y: 0 },
    ],
    right: [
      { x: 0, y: mmToUnits(3) },
      { x: mmToUnits(40), y: mmToUnits(3) },
    ],
  };

  it('alternates between the two rails', () => {
    const points = generateSatin(bar, { density: mmToUnits(0.4), maxStitchLength: mmToUnits(12), minStitchLength: 6 });
    expect(points.length).toBeGreaterThan(50);

    // Successive points must sit on opposite rails (y = 0 or y = 3 mm).
    for (let i = 1; i < Math.min(points.length, 40); i++) {
      expect(Math.abs(points[i].y - points[i - 1].y)).toBeCloseTo(mmToUnits(3), 1);
    }
  });

  it('advances along the column at the requested density', () => {
    const density = mmToUnits(0.4);
    const points = generateSatin(bar, { density, maxStitchLength: mmToUnits(12), minStitchLength: 6 });
    // Points two apart are on the same rail and one density step further on.
    const step = points[2].x - points[0].x;
    expect(unitsToMm(step)).toBeGreaterThan(0.3);
    expect(unitsToMm(step)).toBeLessThan(0.55);
  });

  it('splits crossings that exceed the machine stitch length', () => {
    const wide = {
      left: [
        { x: 0, y: 0 },
        { x: mmToUnits(20), y: 0 },
      ],
      right: [
        { x: 0, y: mmToUnits(25) },
        { x: mmToUnits(20), y: mmToUnits(25) },
      ],
    };
    const points = generateSatin(wide, { density: mmToUnits(0.5), maxStitchLength: mmToUnits(12), minStitchLength: 6 });
    expect(maxSegment(points)).toBeLessThanOrEqual(mmToUnits(12) + 0.001);
  });

  it('derives a column from the outline of an elongated shape', () => {
    const ring = [
      { x: 0, y: 0 },
      { x: mmToUnits(30), y: 0 },
      { x: mmToUnits(30), y: mmToUnits(2) },
      { x: 0, y: mmToUnits(2) },
    ];
    const column = columnFromRing(ring);
    expect(column).not.toBeNull();
    const widths = columnWidths(column!).map(unitsToMm);
    // The rails are the long sides, so the column is ~2 mm wide throughout.
    expect(Math.max(...widths)).toBeLessThan(4);
    expect(Math.min(...widths)).toBeGreaterThan(0.5);
  });
});

describe('running and bean stitch', () => {
  it('spaces running stitches at the requested length', () => {
    const path = [
      { x: 0, y: 0 },
      { x: mmToUnits(20), y: 0 },
    ];
    const points = runningStitch(path, { stitchLength: mmToUnits(2), minStitchLength: 6 });
    expect(points.length).toBe(11);
    expect(unitsToMm(distance(points[0], points[1]))).toBeCloseTo(2, 5);
  });

  it('sews a bean stitch as three passes over the same path', () => {
    const path = [
      { x: 0, y: 0 },
      { x: mmToUnits(10), y: 0 },
    ];
    const single = runningStitch(path, { stitchLength: mmToUnits(2), minStitchLength: 6 });
    const bean = beanStitch(path, 3, { stitchLength: mmToUnits(2), minStitchLength: 6 });
    expect(bean.length).toBe(single.length * 3 - 2);
    // It must finish at the far end, not back at the start.
    expect(bean[bean.length - 1].x).toBeCloseTo(mmToUnits(10), 5);
  });
});

describe('object generation and sequencing', () => {
  const makeObject = (over: Partial<EmbroideryObject>): EmbroideryObject => ({
    id: 'o1',
    name: 'test',
    type: 'fill',
    geometry: { kind: 'polygon', polygon: square(0, 0, mmToUnits(20)) },
    threadIndex: 0,
    angle: 0,
    density: mmToUnits(0.4),
    stitchLength: mmToUnits(3.5),
    underlay: defaultUnderlay('none'),
    order: 0,
    forceTrimBefore: false,
    visible: true,
    notes: [],
    autoGenerated: false,
    ...over,
  });

  it('generates underlay before the top stitching', () => {
    const withoutUnderlay = generateObjectStitches(makeObject({ underlay: defaultUnderlay('none') }));
    const withUnderlay = generateObjectStitches(makeObject({ underlay: defaultUnderlay('edge-run') }));
    expect(withUnderlay.runs.flat().length).toBeGreaterThan(withoutUnderlay.runs.flat().length);
    expect(withUnderlay.runs.length).toBeGreaterThan(withoutUnderlay.runs.length);
  });

  it('inserts a colour change between objects using different threads', () => {
    const objects = [
      makeObject({ id: 'a', threadIndex: 0, order: 0 }),
      makeObject({
        id: 'b',
        threadIndex: 1,
        order: 1,
        geometry: { kind: 'polygon', polygon: square(mmToUnits(30), 0, mmToUnits(20)) },
      }),
    ];
    const { stitches, threadSequence } = assembleStitches(objects);
    const changes = stitches.filter((s) => s.command === StitchCommand.ColorChange);
    expect(changes.length).toBe(1);
    expect(threadSequence).toEqual([0, 1]);
    expect(colorBlocks(stitches).length).toBe(2);
  });

  it('does not insert a colour change between objects sharing a thread', () => {
    const objects = [
      makeObject({ id: 'a', threadIndex: 0, order: 0 }),
      makeObject({
        id: 'b',
        threadIndex: 0,
        order: 1,
        geometry: { kind: 'polygon', polygon: square(mmToUnits(30), 0, mmToUnits(20)) },
      }),
    ];
    const { stitches } = assembleStitches(objects);
    expect(stitches.filter((s) => s.command === StitchCommand.ColorChange).length).toBe(0);
  });

  it('trims when travelling far between objects and sews across when close', () => {
    const far = assembleStitches([
      makeObject({ id: 'a', order: 0 }),
      makeObject({
        id: 'b',
        order: 1,
        geometry: { kind: 'polygon', polygon: square(mmToUnits(60), mmToUnits(60), mmToUnits(20)) },
      }),
    ]);
    expect(countTrims(far.stitches)).toBeGreaterThanOrEqual(1);
  });

  it('terminates the stitch stream with an End command', () => {
    const { stitches } = assembleStitches([makeObject({})]);
    expect(stitches[stitches.length - 1].command).toBe(StitchCommand.End);
    expect(stitches.filter((s) => s.command === StitchCommand.End).length).toBe(1);
  });

  it('produces nothing at all for an empty object list', () => {
    expect(assembleStitches([]).stitches).toEqual([]);
  });

  it('skips invisible objects', () => {
    const visible = assembleStitches([makeObject({ visible: true })]).stitches.length;
    const hidden = assembleStitches([makeObject({ visible: false })]).stitches.length;
    expect(visible).toBeGreaterThan(0);
    expect(hidden).toBe(0);
  });
});

describe('sew order optimisation', () => {
  const obj = (id: string, threadIndex: number, x: number, size = mmToUnits(10)): EmbroideryObject => ({
    id,
    name: id,
    type: 'fill',
    geometry: { kind: 'polygon', polygon: square(x, 0, size) },
    threadIndex,
    angle: 0,
    density: mmToUnits(0.4),
    stitchLength: mmToUnits(3.5),
    underlay: defaultUnderlay('none'),
    order: 0,
    forceTrimBefore: false,
    visible: true,
    notes: [],
    autoGenerated: true,
  });

  it('groups objects by thread to minimise colour changes', () => {
    const objects = [
      obj('a', 0, 0),
      obj('b', 1, mmToUnits(20)),
      obj('c', 0, mmToUnits(40)),
      obj('d', 1, mmToUnits(60)),
    ];
    const result = optimizeOrder(objects);
    const threads = result.objects.map((o) => o.threadIndex);
    // Two colour blocks, not four.
    let changes = 0;
    for (let i = 1; i < threads.length; i++) if (threads[i] !== threads[i - 1]) changes++;
    expect(changes).toBe(1);
    expect(result.colorChanges).toBe(1);
  });

  it('sews a contained object after the shape it sits on', () => {
    const big = obj('big', 0, 0, mmToUnits(40));
    const small: EmbroideryObject = {
      ...obj('small', 1, mmToUnits(10), mmToUnits(10)),
      geometry: { kind: 'polygon', polygon: square(mmToUnits(10), mmToUnits(10), mmToUnits(10)) },
    };
    const result = optimizeOrder([small, big]);
    const ids = result.objects.map((o) => o.id);
    expect(ids.indexOf('big')).toBeLessThan(ids.indexOf('small'));
  });

  it('renumbers order after a manual move', () => {
    const objects = optimizeOrder([obj('a', 0, 0), obj('b', 0, mmToUnits(20)), obj('c', 0, mmToUnits(40))]).objects;
    const moved = moveInOrder(objects, objects[2].id, 0);
    expect(moved[0].id).toBe(objects[2].id);
    expect(moved.map((o) => o.order)).toEqual([0, 1, 2]);
  });
});

describe('geometry helpers', () => {
  it('computes polygon area net of holes', () => {
    const size = mmToUnits(20);
    const hole = mmToUnits(10);
    const area = polygonArea(squareWithHole(size, hole));
    expect(area).toBeCloseTo(size * size - hole * hole, 1);
  });
});
