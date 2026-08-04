# Architecture and readiness audit

Factual map of the codebase as it stands. Every claim here was checked against
the actual source or produced by running the code; where something was measured,
the measurement is given. Nothing in this document changed any behaviour.

Measured at commit `d18799d`.

---

## 1. Architecture

Five layers. Dependencies point downward only — no layer imports from one above
it, and `src/domain` and `src/processing` contain no DOM references, which is why
the whole pipeline runs under Node in the test suite.

```
  src/ui/          React workspace, canvas renderer, simulator, panels   3,224 lines
        │
  src/app/         orchestration: pipeline, editing, export, history     1,003 lines
        │
  src/processing/  analysis, tracing, digitizing, stitch generation      3,196 lines
        │
  src/domain/      units, geometry, stitch, thread, machine, design      1,576 lines
        │
  src/infra/       PES codec, project format, IndexedDB                  1,236 lines

  tests/           146 tests across 6 files                              1,881 lines
  scripts/         fixture + smoke tooling (Node, not shipped)             810 lines
```

Total 12,940 lines of TypeScript/TSX/JS/CSS; 13,310 including docs and fixture
metadata.

### Coordinate system

One decision propagates everywhere: **1 internal unit = 0.1 mm, x right, y
down** (`src/domain/units.ts`). That is exactly the PEC/PES storage convention,
so no axis flip or scale conversion happens at export time. Renderer, validator,
digitizer and encoder all work in the same space.

---

## 2. Subsystems

| Subsystem | Location | Responsibility |
|---|---|---|
| Units & geometry | `src/domain/units.ts`, `geometry.ts` | 0.1 mm unit system; points, rings, polygons with holes, area/winding, ring offsetting, resampling |
| Stitch model | `src/domain/stitch.ts` | `StitchCommand` (Stitch/Jump/ColorChange/End), colour-block splitting, trim counting, thread-length measurement |
| Stitch sequence | `src/domain/stitch-sequence.ts` | The single artifact preview, validation, status and export all consume; resolves block→thread and carries a content digest |
| Thread system | `src/domain/thread.ts` | Thread records, redmean colour distance, nearest-thread mapping, Brother PEC 64-colour chart, chart search/registration |
| Machine profiles | `src/domain/machine.ts` | `MachineProfile`/`Hoop`; Brother SE700 (5×7 and 4×4) and a generic 4×4 |
| Design model | `src/domain/design.ts` | `EmbroideryDesign`; all statistics computed from the stitch array |
| Readiness | `src/domain/readiness.ts` | Three-tier reporting; machine tier hard-pinned to `not-performed` |
| Image analysis | `src/processing/image/` | Raster ops, colour quantization, region segmentation, artwork analysis |
| Tracing | `src/processing/trace/` | Crack-following contour tracing with hole nesting; simplification and smoothing |
| Digitizing | `src/processing/digitize/digitizer.ts` | Regions → `EmbroideryObject`s with stitch type, thread, angle, density, underlay |
| Stitch generation | `src/processing/stitches/` | Fill, satin, running/bean, underlay, and the sequencer that emits the stitch stream |
| Order optimisation | `src/processing/optimize/order.ts` | Layering, thread grouping, nearest-neighbour travel |
| Validation | `src/processing/validate/validate-design.ts` | ~20 rules at INFO/WARNING/ERROR |
| PES codec | `src/infra/pes/` | Binary reader/writer, PEC block encoder, PES v1 writer, PES/PEC reader |
| Persistence | `src/infra/project/`, `src/infra/storage/` | Versioned project format (v3, with migrations), IndexedDB store |
| Application layer | `src/app/` | Pipeline orchestration, editor operations, undo/redo, export workflow, worksheet |
| UI | `src/ui/` | Workspace, canvas renderer, simulator timeline, three panels, dialogs |

---

## 3. Responsibility map

| Concern | File | Key functions |
|---|---|---|
| Image upload | `src/app/image-decode.ts` | `decodeArtworkFile`, `loadImage`, `bytesToBase64`, `decodeStoredArtwork` |
| Image preprocessing | `src/processing/image/raster.ts` | `fitWithin`, `resize`, `toGrayscale`, `sobelMagnitude`, `distanceTransform` |
| Artwork analysis | `src/processing/image/analysis.ts` | `analyzeArtwork`, `measureGradients`, `channelDelta` |
| Colour detection | `src/processing/image/quantize.ts` | `quantize`, `histogram`, `seedCentroids`, `estimateDistinctColors` |
| Artwork segmentation | `src/processing/image/segment.ts` | `extractRegions`, `cleanMask`, `morph`, `detectBackgroundColorIndex` |
| Contour tracing | `src/processing/trace/contour.ts` | `traceRings`, `maskToPolygons`, `nextEdge`, `ringCentroidInside` |
| Outline simplification | `src/processing/trace/simplify.ts` | `simplifyRing`, `smoothRing`, `simplifyPolygon`, `smoothPolygon`, `transformPolygon` |
| Automatic digitization | `src/processing/digitize/digitizer.ts` | `digitize`, `classify`, `buildObject`, `defaultDigitizeOptions` |
| Embroidery object creation | `src/domain/embroidery-object.ts` + `digitizer.ts` | `buildObject`, `defaultUnderlay`, `mapGeometry`, `cloneObject` |
| Fill stitch generation | `src/processing/stitches/fill.ts` | `generateFill`, `decomposeIntoCells`, `orderCells`, `rowStitches`, `scanlineIntersections`, `travelAlongRing`, `segmentInside` |
| Running stitch generation | `src/processing/stitches/running.ts` | `runningStitch`, `outlineStitch`, `beanStitch`, `dropShortSegments`, `enforceMaxStitchLength` |
| Satin stitch generation | `src/processing/stitches/satin.ts` | `generateSatin`, `columnFromRing`, `columnFromPrincipalAxis`, `columnFromFarthestPair`, `columnWidths`, `columnCenterline` |
| Underlay generation | `src/processing/stitches/underlay.ts` | `polygonUnderlay`, `satinUnderlay` |
| Stitch sequencing | `src/processing/stitches/generate.ts` | `assembleStitches`, `generateObjectStitches` |
| Sew order | `src/processing/optimize/order.ts` | `optimizeOrder`, `moveInOrder` |
| Jump handling | `generate.ts` (`assembleStitches`), `pec.ts` (`pecEncode`) | jump emitted when travel > `trimThreshold` (6 mm) or a colour change breaks the thread |
| Trim handling | `src/domain/stitch.ts` (`countTrims`), `pec.ts` (`pecEncode`) | PEC has no trim opcode; any jump after stitching has begun is encoded as a trim-jump |
| Thread/colour handling | `src/domain/thread.ts`, `stitch-sequence.ts`, `infra/pes/pec.ts` | `nearestThread`, `colorDistance`, `buildStitchSequence`, `buildUniquePalette`, `nearestPecIndex` |
| Stitch rendering | `src/ui/render/stitch-renderer.ts` | `render`, `renderTimeline`, `drawHoop`, `drawGrid`, `fitViewport`, `designToScreen` |
| Stitch simulation | `src/ui/App.tsx` + `src/ui/Timeline.tsx` | rAF loop at `App.tsx:394-410`, `jumpColor`, `Timeline` transport controls |
| Design editing | `src/app/editor-ops.ts` | `moveObject`, `scaleObject`, `rotateObject`, `deleteObject`, `duplicateObject`, `setObjectProperty`, `setStitchType`, `setStartPoint`, `reversePath`, `replaceThread`, `centerDesign`, `scaleDesign` |
| Undo/redo | `src/app/history.ts` | `createHistory`, `push`, `undo`, `redo`, `canUndo`, `canRedo` |
| Validation | `src/processing/validate/validate-design.ts` | `validateDesign`, `validateHoopFit` |
| PES encoding | `src/infra/pes/pes-writer.ts`, `pec.ts`, `binary.ts` | `writePes`, `writeBlocks`, `writeSewSegHeader`, `buildSegments`, `writePec`, `pecEncode`, `writeValue`, `BinaryWriter` |
| PES decoding/verification | `src/app/export-pes.ts`, `src/infra/pes/pes-reader.ts` | `verifyPesBytes`, `readPes`, `decodeStitches` |
| PES import | `src/infra/pes/pes-reader.ts`, `src/ui/App.tsx` | `readPes`, `readPecBlock`, `handleImportStitchFile` |
| Project save | `src/infra/project/project-format.ts`, `storage/project-store.ts` | `serializeProject`, `projectToJson`, `saveProject` |
| Project reload | same | `deserializeProject`, `migrate`, `projectFromJson`, `loadProject`, `openProjectState` |

---

## 4. One workflow, traced through the code

Upload of a PNG through to an independently verified PES file.

**1. Upload** — `LeftPanel.tsx` dropzone → `App.handleUpload` →
`decodeArtworkFile` (`app/image-decode.ts`). Extension and MIME are checked, the
raw bytes are kept verbatim as base64 (the original is never overwritten), the
file is drawn to an offscreen canvas and `getImageData` produces a
`RasterImage` — a plain `{width, height, Uint8ClampedArray}`. **This is the only
DOM contact in the artwork path.**

**2. Preprocessing** — `pipeline.analyze` → `analyzeArtwork`
(`processing/image/analysis.ts`) → `fitWithin(source, 320)` downscales the
longest side to 320 px with an alpha-weighted box filter (`raster.ts:resize`).
`pipeline.fitArtworkToCanvas` computes the physical target box, preserving
aspect ratio.

**3. Analysis** — `analyzeArtwork` runs:
- `quantize(img, colorCount)` — histogram at 5 bits/channel, deterministic
  k-means++ seeding (`seedCentroids`), weighted k-means, labels per pixel.
- `detectBackgroundColorIndex` — the colour holding ≥75 % of the border.
- `extractRegions(q, minPixels)` — 4-connected labelling per colour class; each
  region gets a distance transform for `maxHalfWidth`/`medianHalfWidth`.
- `sobelMagnitude` → edge density; `measureGradients` → gradient share;
  `estimateDistinctColors` → source colour estimate.
- Physical width per region is computed in mm, and each region is classified
  `running` / `satin` / `fill`. Output: `ArtworkAnalysis`, carrying the
  quantization and region data forward so it is not recomputed.

**4. Segmentation → geometry** — `digitize` (`digitizer.ts`) per region:
`cleanMask` (erode→dilate→erode) → `maskToPolygons` (`trace/contour.ts`), which
follows the cracks between foreground and background so rings close exactly and
holes nest by winding direction. Falls back to the untouched mask when speckle
removal annihilates a thin shape. Then `simplifyPolygon(0.8)` →
`smoothPolygon(2)` → `simplifyPolygon(0.35)` → `transformPolygon(toDesign)`,
mapping pixels to 0.1 mm units.

**5. Objects** — `classify` picks the stitch type from measured width,
elongation and hole count; `buildObject` produces an `EmbroideryObject` with
geometry, thread index, angle, density, stitch length and underlay. Threads come
from `nearestThread` over the PEC chart, with palette slots reused when two
artwork colours map to the same cone. `centerObjectsInHoop` (`pipeline.ts`)
centres the group; `optimizeOrder` layers, groups by thread and orders
nearest-neighbour.

**6. Stitch sequence** — `regenerateWithNotes` → `assembleStitches`
(`stitches/generate.ts`). Per object, `generateObjectStitches` emits underlay
runs then top stitching (`generateFill` / `generateSatin` / `runningStitch` /
`beanStitch`), passes every run through `enforceMaxStitchLength` and
`dropShortSegments`, and the sequencer joins runs — stitching across short hops,
jumping (thus trimming) beyond 6 mm, inserting `ColorChange` between threads and
`End` at the finish. It returns `threadSequence`, stored as
`design.colorSequence`. `validateDesign` runs immediately.

**7. Preview** — `App.tsx:100` builds the `StitchSequence` once;
`DesignCanvas` → `stitch-renderer.render` draws each colour block in
`blockThreads[blockIndex]`, plus hoop, safe area, jumps and needle position.
Nothing draws the source image in stitch mode.

**8. Validation** — `validateDesign` checks empty design, non-finite
coordinates, missing `End`, hoop fit (error) and safe area (warning), stitch
length against `machine.maxStitchLength`, short stitches, stitch count, palette
consistency via `buildStitchSequence`, colour changes, trims, and per-object
density/satin-width/tiny-object rules.

**9. PES** — `exportPes` (`app/export-pes.ts`) blocks on errors, requires
warning acknowledgement, then `writePes(sequence.stitches,
sequence.blockThreads, …)`. `pes-writer.ts` writes the signature, patches the
PEC offset, writes the CEmbOne/CSewSeg vector section, then `writePec` writes
the PEC header, palette, stitch block via `pecEncode`, and the thumbnails.

**10. Verification** — `verifyPesBytes` decodes the bytes it just wrote with
`readPes` and compares signature, stitch count, colour block count, **colour
order** and dimensions against the sequence. Any failure suppresses the
download. Independently, `tests/pes.test.ts` and
`tests/fixtures-regression.test.ts` shell out to `pyembroidery` and compare the
same quantities.

---

## 5. The ten most important files

1. **`src/processing/stitches/generate.ts`** (276) — the only place geometry
   becomes needle penetrations. Owns jumps, trims, colour changes and the `End`
   command. Everything downstream is a consumer of its output.
2. **`src/infra/pes/pec.ts`** (305) — the PEC block is what the machine actually
   reads. `pecEncode` is the byte-level stitch encoder; `buildUniquePalette`
   decides which machine colour slot each thread occupies.
3. **`src/domain/stitch-sequence.ts`** (165) — the contract that preview,
   validation, status and export describe the same design. Resolves block→thread
   once and digests it.
4. **`src/processing/stitches/fill.ts`** (366) — fill is the bulk of almost every
   design's stitch count. The boustrophedon decomposition here is what keeps
   trims low.
5. **`src/processing/digitize/digitizer.ts`** (337) — decides stitch type,
   thread, angle and density per region. Every quality judgement the automatic
   pass makes is here.
6. **`src/infra/pes/pes-writer.ts`** (226) — the PES v1 container and CSewSeg
   section; determines whether third-party software can read the file.
7. **`src/processing/image/analysis.ts`** (378) — turns pixels into the
   structured description everything downstream branches on.
8. **`src/processing/validate/validate-design.ts`** (346) — the only thing
   standing between a bad design and a machine.
9. **`src/domain/units.ts`** (37) — small, but it fixes the coordinate
   convention that makes the encoder trivially correct. A change here breaks
   everything silently.
10. **`src/app/export-pes.ts`** (217) — the gate: validation, encoding and
    post-write verification in one place.

---

## 6. Code classification

| Category | Files | Lines | Notes |
|---|---|---|---|
| **Core production** | `src/domain/*` (except `readiness.ts`), `src/processing/**`, `src/infra/pes/*`, `src/infra/project/*` | ~5,700 | The engine. Runs headless. |
| **Application layer** | `src/app/*` | 1,003 | Orchestration only; no algorithms. |
| **UI** | `src/ui/**`, `src/main.tsx` | 3,235 | React + canvas. `styles.css` alone is 710. |
| **Reporting** | `src/domain/readiness.ts`, `src/app/worksheet.ts` | 395 | Produces status/readiness text; no engine behaviour. |
| **Infrastructure** | `src/infra/pes/binary.ts`, `src/infra/storage/project-store.ts` | 284 | Byte I/O and IndexedDB. |
| **Test code** | `tests/**` | 1,881 | 146 tests. |
| **Test tooling** | `scripts/*.mjs` | 810 | Node only, never bundled. |
| **Experimental** | — | 0 | None. |
| **Placeholder / mock** | — | 0 | A source-wide scan for mock/fake/stub/dummy/TODO/FIXME returns only the two honest "text detection is not implemented" strings. |

**Dead surface area:** 23 exported symbols are never imported anywhere —
`mergeBounds`, `distanceSq`, `ringPerimeter`, `orientRing`, `rotatePoints`,
`translatePoints`, `scalePoints`, `pathNormal`, `formatLength`, `rgbToInt`,
`hexToRgb`, `pecPaletteIndex`, `registerChart`, `registerMachine`, `renumber`,
`reorderObject`, `updateObject`, `undoLabel`, `redoLabel`, `designBounds`,
`designColorBlocks`, `totalJumpDistance`, `getPixel`. Harmless (tree-shaken from
the bundle) but it is unused API surface, and `registerChart`/`registerMachine`
are advertised extension points with no caller.

---

## 7. Is automatic digitization genuinely dynamic?

**Yes.** Verified by running five different artworks through the real pipeline
and measuring the output:

| Artwork | Stitches | Objects | Types | Cones | Size | Sequence |
|---|---:|---:|---|---:|---|---|
| 60×60 px blue square | 337 | 1 | fill | 1 | 21.0 × 21.0 mm | `b3aff67b` |
| 140×140 px blue square | 1,590 | 1 | fill | 1 | 49.0 × 49.0 mm | `3f216983` |
| two 65×140 px blocks | 1,593 | 2 | fill, fill | 2 | 49.0 × 49.0 mm | `eb74d229` |
| 160×5 px black bar | 306 | 1 | **satin** | 1 | 56.0 × 1.7 mm | `4e4cf3ec` |
| green annulus | 1,892 | 1 | fill (1 hole) | 1 | 59.1 × 59.1 mm | `80329af8` |

The causal chain, function by function:

1. **Pixels decide colours.** `quantize` clusters the actual RGB histogram.
   Different colours produce different centroids, so `analysis.detectedColors`
   changes.
2. **Colours decide regions.** `extractRegions` does connected-component
   labelling on those labels. Two blocks instead of one produce two regions and
   therefore two objects.
3. **Region shape decides stitch type.** `classify` reads
   `region.maxHalfWidth`/`medianHalfWidth` — from a distance transform of the
   actual mask — converts to millimetres at the requested physical size, and
   picks running (< 1.6 mm), satin (≤ 10 mm and elongation ≥ 1.8) or fill. The
   5 px bar became satin purely because its measured width crossed a threshold.
4. **Region outline decides geometry.** `maskToPolygons` traces the real mask;
   the annulus produced a polygon with a genuine hole.
5. **Geometry decides stitch coordinates.** `generateFill` scans the actual
   polygon: number of rows = shape extent ÷ density; row length = the real
   scanline span. Stitch count therefore scales with area — the 2.33× linear
   scale-up produced a 4.72× stitch increase against a 5.44× area ratio
   (sub-linear because row-end effects do not scale with area).
6. **Colours decide threads and stops.** `nearestThread` maps each artwork
   colour to the nearest chart entry by redmean distance; the two-block artwork
   produced two cones and one colour change.

Every one of the five produced a different sequence digest. There is no
lookup table, no template, and no fixed output anywhere in the path.

---

## 8. Do preview, simulator, validation and exporter share one StitchSequence?

**Effectively yes — the data is provably identical — but the coupling is by
convention in two places rather than enforced by types.**

What is verified:

- `buildStitchSequence` sets `stitches: design.stitches` — the *same array
  reference*, not a copy (`stitch-sequence.ts:71`).
- `App.tsx:100` builds the sequence once with `useMemo`.
- `export-pes.ts:64` calls `buildStitchSequence(design)` and encodes
  `sequence.stitches` and `sequence.blockThreads`.
- `validate-design.ts:171` calls it for palette consistency.
- `readiness.ts:65` calls it for status.
- `export-pes.ts` records `sequenceId`; `ExportResult.sequenceId` is compared
  against the live preview digest in the UI and in the readiness report, so a
  design edited after export is reported as stale.
- A test asserts `result.sequenceId === buildStitchSequence(design).id` for
  every fixture and in `tests/color-sequence.test.ts`.

The two soft spots, stated precisely:

1. **`DesignCanvas` and `Timeline` take `stitches` and `blockThreads` as two
   separate props** (`App.tsx:560`, `571`) rather than the sequence object.
   Because `sequence.stitches` *is* `design.stitches`, the rendered data is
   identical today. But nothing in the type system prevents a future caller
   passing a stitch array from one design and block threads from another.
2. **`App.tsx:110` is a residual instance of the block-ordinal/palette-index
   confusion** that was fixed elsewhere: the selection highlight does
   `colorBlocks(design.stitches)[selectedObject.threadIndex]`. For a design that
   returns to a colour, this highlights the wrong block. It affects only the blue
   selection overlay — not the preview colours, not the export — but it is the
   same bug class and should be closed.

---

## 9. PES implementation audit

**Version generated: PES version 1** (`#PES0001`), containing a PEC stitch
block. Chosen because it is the most widely readable variant.

**What is implemented:**

| Part | Status |
|---|---|
| `#PES0001` signature + uint32 PEC offset | implemented, offset back-patched |
| PES v1 header (scale-to-fit, hoop selector, block count) | implemented |
| `CEmbOne` block + sew-seg header (bounds, affine transform, extents) | implemented |
| `CSewSeg` segment list with `0x8003` section terminators and colour log | implemented |
| PEC header: `LA:` label, palette table, padding to 0x200 | implemented |
| PEC stitch block header (length, `0x31 FF F0`, width/height, `0x1E0`/`0x1B0`) | implemented |
| PEC variable-length stitch encoding | implemented |
| PEC thumbnails: one overall + one per colour block, 48×38 1-bit | implemented |
| PES v6 metadata, programmable fills, motif patterns, feather | **not implemented** (v1 has no such fields) |
| Reading the PES vector section on import | **not implemented** — import goes via the PEC block only |

**Thread colours.** Two separate mappings:
- `buildUniquePalette` (`pec.ts`) assigns each design thread a machine palette
  slot 1–64, consuming slots so two different colours never collapse onto one —
  which is what makes the machine's colour list match the design.
- `nearestPecIndex` supplies the colour code for CSewSeg segments.
Both use `colorDistance` — the redmean metric, matching pyembroidery/libembroidery,
so palette indices agree with other embroidery software.

**Jumps and trims.** PEC has no trim opcode. `pecEncode` writes the *first*
move of a design with `JUMP_CODE` (0x10) — positioning only — and every
subsequent move with `TRIM_CODE` (0x20), which is how a PEC file expresses "cut
and move". `countTrims` in `src/domain/stitch.ts` counts exactly the same way,
so the trim count the UI reports is the trim count the machine will perform.

**Long stitches.** `writeValue` uses the 1-byte form for deltas in −63…62 and
otherwise a 2-byte form carrying a 12-bit signed value with bit 15 set. Ordinary
stitches never approach the limit: `enforceMaxStitchLength` splits at 12 mm
during generation and the validator raises an ERROR above
`machine.maxStitchLength` (12.7 mm).

**Design dimensions.** Not stored as a declared size — they are implicit in the
stitch coordinates, plus a width/height pair in the PEC block header
(`round(maxX − minX)`, `round(maxY − minY)`) and the extents in the CEmbOne
header. Verification measures dimensions from the *decoded stitches*, which is
the meaningful check.

**Multi-colour sequences.** A colour change is `0xFE 0xB0` followed by an
alternating `0x02`/`0x01` byte. The palette table in the PEC header holds
`count−1` followed by one slot byte per colour block, so a design that returns
to a colour correctly emits two blocks pointing at the same slot.

**Independent validation.** Three layers:
1. In-app: `verifyPesBytes` decodes the written bytes with the application's own
   reader and compares signature, stitch count, colour block count, colour order
   and dimensions. Failure suppresses the download.
2. Automated: `tests/pes.test.ts` (18 tests) and
   `tests/fixtures-regression.test.ts` shell out to **pyembroidery**, an
   independent implementation, and compare stitch counts, thread colours, trim
   counts, long jumps, multi-colour ordering and extents.
3. Foreign-file: the reader is tested against PES v1, PES v6 and bare PEC files
   *written by pyembroidery*, so it is not merely self-consistent.

### Defect found during this audit

**A jump delta of ±2048 or more on a single axis silently wraps.** Measured by
round-tripping synthetic designs:

```
jump dy=1800 -> decoded max y = 1800  OK
jump dy=2047 -> decoded max y = 2047  OK
jump dy=2100 -> decoded max y = 0     *** WRAPPED ***
jump dy=3000 -> decoded max y = 0     *** WRAPPED ***
```

The 12-bit field holds −2048…2047 (±204.7 mm). There is no guard in `writeValue`,
`pecEncode` or the validator — `grep` for a jump-length check in
`validate-design.ts` returns nothing.

**This cannot trigger on the shipped machine profiles.** The largest hoop is
130 × 180 mm, so the largest possible per-axis delta inside a hoop-fitting
design is 1800 units — and designs that exceed the hoop are already blocked with
an ERROR. It is a latent trap, not a live bug: **adding any hoop taller or wider
than 204.7 mm would silently corrupt files** with no error anywhere.

---

## 10. Brother SE700 readiness

### VERIFIED BY CODE (structure guarantees it)

- Internal units are the PES storage units, so no scale/axis conversion happens
  at export (`units.ts`).
- The exporter encodes `sequence.stitches` — the same array the preview renders.
- Machine readiness is hard-pinned to `not-performed`; no code path sets it
  otherwise (`readiness.ts:machineTier`).
- ERROR-level validation blocks export unconditionally; warnings require
  explicit acknowledgement (`export-pes.ts`).
- A failed post-export check suppresses the download rather than warning.
- No mock, stub or placeholder exists in `src/` (scanned).

### VERIFIED BY AUTOMATED TEST (146 tests)

- PES v1 signature, PEC offset validity, binary-not-text, CEmbOne/CSewSeg present.
- Stitch count, thread colours, colour order, trim commands, long jumps and
  extents all agree with **pyembroidery**.
- Reading PES v1, PES v6 and PEC files written by pyembroidery.
- Truncated and non-PES files are rejected with specific messages.
- PEC palette uniqueness and slot reuse.
- Fill covers at the requested density, honours angle, never crosses a hole,
  respects the stitch-length limit.
- Satin alternates rails, advances at the requested density, splits over-long
  crossings, keeps a bar at constant width with square ends.
- Colour changes only between different threads; a returned-to colour resolves
  and exports correctly.
- Empty, blank, oversized, NaN-coordinate and over-long-stitch designs are all
  rejected with quoted measurements.
- Project round-trip is byte-identical on re-export; artwork bytes preserved;
  format v1→v3 migration.
- Undo/redo including redo-stack clearing and bounded history.
- Six fixtures match golden metrics exactly (850 / 1,418 / 2,151 / 3,273 /
  1,862 / 5,923 stitches).

### VERIFIED BY BROWSER TEST (real Chromium, built app)

- Project creation, drag-and-drop upload of PNG, JPEG and SVG.
- Analysis, digitizing, and a non-blank stitch preview drawn from stitch data.
- Simulator playback; density edit changes stitch count; undo restores it.
- Validation messages contain measured values, never "something went wrong".
- Export downloads a file beginning `#PES0001`; all post-export checks pass.
- Save → page reload → reopen preserves the design exactly.
- Re-import of the app's own exported PES.
- Oversized design disables export and quotes the overage in inches.
- Unsupported file rejected with a specific message. Zero console errors.
- Browser-produced stitch counts are **identical** to the headless pipeline for
  all six fixtures — the UI and the engine agree byte for byte.

### NOT YET VERIFIED

- That any third-party embroidery *application* (PE-Design, Embrilliance,
  Ink/Stitch) opens these files. Only pyembroidery has been used.
- Behaviour with photographic or gradient artwork beyond the analyser's warning.
- Designs above ~6,000 stitches; the largest fixture is 5,923.
- Density behaviour on anything other than the assumed stable fabric.
- Whether a jump beyond 204.7 mm per axis can ever be produced (see §9).

### REQUIRES PHYSICAL MACHINE TEST

Everything about thread, fabric and needle. Specifically:

- Whether the SE700 lists and loads the file from USB at all.
- Whether the machine's displayed stitch count and colour-stop count match the
  software's prediction.
- Registration between colour areas after a re-thread.
- Whether fill density perforates or under-covers real fabric.
- Whether satin columns hold at the generated widths.
- Whether the finished piece measures the predicted size.
- Whether trims and jumps behave as the file intends.
- Puckering and stabiliser interaction.

**No Brother SE700 compatibility claim is made or supported by anything in this
repository.**

---

## 11. Biggest technical risks

Ranked by likelihood × consequence.

1. **No pull compensation.** Confirmed absent (`grep` for compensat* in `src/`
   returns nothing). Thread pulls fabric inward as it sews, so every column and
   fill comes out slightly narrower than designed and adjacent colour areas open
   up gaps. The preview cannot show this because it draws ideal geometry. **This
   is the single most likely cause of "looked right, sewed wrong".**
2. **Adjacent regions share an exact boundary with no overlap.** The digitizer
   traces each colour region independently, so two touching areas meet at a
   mathematical edge with zero overlap. Combined with risk 1, visible fabric
   shows between colours. Already visible as white slivers in the fixture
   previews.
3. **Density is a fixed default, blind to fabric.** 0.4 mm row spacing is
   applied regardless of substrate. Correct for stable woven; too dense for
   knits, where it perforates.
4. **Underlay is generated but never verified on fabric.** It changes stitch
   count and is present in the file, but whether edge-run at 0.8 mm inset
   actually stabilises real fabric is untested.
5. **The 12-bit jump wrap (§9).** Dormant on current profiles; silent corruption
   the moment a larger hoop is added. No error would be raised anywhere.
6. **Small-feature warnings are advisory.** Features under 1 mm are stitched as
   running lines and warned about, but export proceeds. An operator who
   acknowledges warnings in bulk will sew unreadable detail.
7. **Runtime and thread-length figures are estimates presented numerically.**
   650 spm and 45 s per colour change are assumptions; they read as precise.
8. **Satin rails from the principal axis assume a genuinely elongated shape.**
   A curved or L-shaped narrow region will produce rails that do not follow the
   stroke. No fixture covers a curved satin path.

---

## 12. Three highest-priority quality improvements

1. **Pull compensation + adjacent-region overlap.** Widen satin columns by a
   configurable amount (typically 0.2 mm per side) and expand each filled region
   slightly so neighbours overlap rather than abut. This addresses risks 1 and 2
   together and is the difference between "recognisable" and "clean" on real
   fabric. It is a contained change in `digitizer.ts` and `satin.ts`.
2. **Fabric presets driving density and underlay.** A small table
   (woven / knit / piqué / cap) selecting row spacing, underlay type and minimum
   feature size, applied at digitize time. Removes the largest source of
   density-related failures and needs no new algorithms.
3. **A jump-length guard.** Split any per-axis delta above a safe threshold into
   multiple moves in `pecEncode`, and add a validator rule. Small, purely
   defensive, and removes a class of silent file corruption before more machine
   profiles are added.

Deliberately *not* on this list: text detection, additional stitch types, or any
re-architecture. None of them would change what comes out of the needle as much
as the three above.

---

## A. What is genuinely working

The full path from artwork to a verified PES file. Analysis, colour reduction,
segmentation, tracing with holes, stitch-type classification, fill, satin,
running and bean stitch, underlay, sew ordering, sequencing with jumps, trims
and colour changes, validation, PES v1 encoding, post-write verification, PES
import, project persistence with migration, undo/redo, and a preview and
simulator drawn from the real stitch data. Digitization is demonstrably
data-driven, not templated. 146 automated tests, a browser test on the built
app, and independent cross-checks against pyembroidery. No mocks or placeholders
anywhere in `src/`.

## B. Working but needs real-world validation

Everything about how the numbers behave in thread. Fill density (0.4 mm),
underlay effectiveness, satin column widths, the 1 mm minimum feature threshold,
trim/jump behaviour on the machine, colour registration after a re-thread,
predicted vs measured finished size, and the runtime and thread-length
estimates. Also: whether third-party embroidery software opens the files.

## C. What is incomplete

No pull compensation. No adjacent-region overlap. No fabric-aware density. No
text detection (stated in the UI). Imported PES designs are stitches only — the
vector section is not parsed, so they cannot be re-digitized. Only the Brother
PEC chart is bundled; Madeira and Isacord are deliberately absent rather than
guessed. `registerChart`/`registerMachine` are extension points with no caller.
No appliqué sequencing. The 12-bit jump guard is missing. `App.tsx:110` still
carries the block-ordinal/palette-index confusion in the selection highlight.

## D. What to physically test first

**Fixture 01, the single-colour square, on stable woven cotton with tear-away
stabiliser.** Before sewing, compare three numbers on the machine's own display
against the worksheet: **stitch count (850), colour stops (1), and design size
(35 × 35 mm)**. If those three match, the encoder is correct and every later
failure is a density, fabric or tension question rather than a file question. If
any of them disagrees, stop — that is an encoder defect and the exact numbers
are the most valuable diagnostic this project can receive.

Then, in order: 02 (one colour change, check registration), 05 (satin widths),
04 (holes), 03, 06.

## E. The single highest-priority next engineering task

**Pull compensation together with adjacent-region overlap.**

It is the one change that most closes the gap between what the preview shows and
what the needle produces, it is the most likely explanation for the first
disappointing stitch-out, and it is contained enough not to disturb the frozen
engine — a widening parameter in satin generation and a small outward offset per
filled region, both already expressible with existing geometry helpers
(`offsetRing`).

It should be done *after* the first physical test of fixture 01, not before: the
correct compensation value is measured from fabric, and guessing it without a
stitch-out would just be a different set of invented numbers.
