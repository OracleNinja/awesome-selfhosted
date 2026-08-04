# Norton Thread Co. — Embroidery Digitizing

Turn customer artwork into a validated, machine-ready PES embroidery file.

```
UPLOAD IMAGE → DIGITIZE AUTOMATICALLY → REVIEW → EXPORT PES → SEW
```

Everything runs locally in the browser. There is no server, no upload of
customer artwork to a third party, and no external service in the export path.

## The two views

Digitizing starts on its own as soon as artwork finishes decoding, so the
shortest path through the app is: name the project, drop in a PNG, look at the
stitch preview, press **Export PES**. Nobody has to know what tatami, underlay
or a satin column is to get a first-pass design.

**Simple view** (the default) shows the artwork, a plain-English sentence about
how well it suits automatic digitizing, the preview, and whatever must be fixed
before the file can go to a machine. Where a validation problem has exactly one
correct answer — the design overruns the hoop, sits outside the safe area, has a
fill below minimum density, or has stitches longer than the machine allows — the
app offers a one-click fix. Anything needing judgement is not offered as a fix.

**Advanced view** adds colour count, fill row spacing, underlay, the object list
and per-object stitch type, angle, density and thread, the raw analysis metrics
and the object sequencing. It hides nothing that Simple view shows; the toggle
is in the top bar. No number is calculated differently in one view or the other.

The full pipeline underneath is unchanged:

```
IMAGE → ANALYZE → DIGITIZE → PREVIEW → VALIDATE → EDIT → EXPORT PES
```

## Running it

```bash
npm install
npm run dev      # development server on http://localhost:5173
npm run build    # type-check and produce dist/
npm run preview  # serve the production build
```

Verification:

```bash
npm run typecheck
npm run lint
npm test         # 146 tests, including PES round-trips against pyembroidery
```

The PES tests shell out to Python and need `pyembroidery` installed
(`pip install pyembroidery`). It is used only as an independent check on the
files this application writes — the application itself does not depend on it.

There is also a browser test that drives the real UI end to end:

```bash
npm run preview &                                  # in one shell
node scripts/smoke.mjs http://127.0.0.1:4173/ ./out # in another
```

It creates a project, uploads a real PNG, waits for the automatic digitize,
switches to Advanced view, runs the simulator, edits
an object, undoes the edit, validates, exports a `.pes`, saves the project,
reloads the page, reopens the project and re-imports the exported file. It
fails if the downloaded file is not a real PES file or if any post-export check
fails.

## Physical machine validation

**The engine is frozen.** The pipeline below is treated as a working foundation
and is not rewritten except to fix a demonstrated defect.

The current objective is proving it in the real world. Read
[`docs/PHYSICAL-VALIDATION.md`](docs/PHYSICAL-VALIDATION.md) — it is the
step-by-step Brother SE700 procedure, the six-design test set, and the table of
what each failure mode points at.

Three tiers are reported separately and never merged into one "ready" claim:

| Tier | Established by | Status |
|---|---|---|
| Software validated | The validator, on every edit | per design |
| PES format validated | Post-export decode + `pyembroidery` cross-check | per design |
| **Machine validated** | A physical stitch-out recorded by an operator | **NOT PERFORMED** |

The application shows `HARDWARE NOT VERIFIED` in the title bar and reports the
machine tier as `not-performed`. No code path can set it to passed — a test
asserts that for all six fixtures.

### Controlled test designs

Six designs of increasing difficulty live in `fixtures/`, ordered so a physical
failure points at a specific part of the engine rather than "it did not work":

| # | Fixture | Proves |
|---|---|---|
| 1 | single colour square | fill baseline, no colour change |
| 2 | two colour logo | exactly one colour change, in register |
| 3 | multi colour logo | colour grouping across several changes |
| 4 | holes and cut-outs | the needle travels around openings |
| 5 | narrow satin bars | satin columns hold width and square ends |
| 6 | detailed badge | small counters, a colour used then returned to |

```bash
npm run fixtures:artwork   # regenerate the artwork PNGs (they are committed)
npm run fixtures:golden    # re-record fixtures/golden.json after a reviewed change
npm run preview &          # then, against the built app:
npm run fixtures:build     # write fixtures/out/<id>/{artwork,preview,design.pes,worksheet}
```

`fixtures:build` drives the real application in a browser, so `preview.png` is
captured from the application's own canvas rather than re-rendered. That gives
the four things to compare per design: **artwork → preview → PES → physical
stitch-out**, the last recorded on the worksheet the app exports.

`fixtures/golden.json` records what the engine does today. The regression suite
asserts every fixture still matches it exactly, so any change in digitizing
behaviour shows up as a test failure and can be tied to a stitch-out result.

## What the application does

**Analysis.** Colour reduction (weighted k-means over a colour histogram with
farthest-point seeding), connected-region segmentation, contour tracing,
distance-transform width measurement, edge and gradient measures. It reports a
suitability score and warns when the artwork will need manual cleanup.

**Automatic digitizing.** Regions become embroidery objects: solid areas become
tatami fills, long narrow areas become satin columns, and areas too thin to
fill become running-stitch lines. Objects are ordered so shapes that sit on top
of others sew later, and objects sharing a thread sew together.

**Stitch generation.** Real stitch coordinates, in 0.1 mm units:

- tatami fill with boustrophedon cell decomposition, row stagger, and hidden
  travel between cells so the needle rarely has to trim;
- satin columns with rails derived from the shape's principal axis, so a bar
  keeps square ends and constant width;
- running and bean stitch;
- edge-run, centre-run and zigzag underlay.

**One stitch sequence.** The preview, the timeline, the statistics, the status
panel and the PES encoder all consume a single `StitchSequence`, built once by
`buildStitchSequence()`. It carries a content digest of the exact stitches and
threads; the app shows that digest next to both the preview and the exported
file, so the two can be proven to come from the same data rather than assumed
to. A design that has changed since its last export says so.

**Preview and simulation.** The canvas draws the generated stitches, in sew
order, in their thread colours — never the source image. Zoom, pan, fit to
hoop, actual size, hoop boundary, safe area, jump display. The simulator plays,
pauses, steps, seeks and jumps between colour changes.

**Editing.** Select, move, scale, rotate, delete, duplicate, reorder, change
stitch type, density, angle, stitch length, underlay and thread; shift the
start point; reverse a path. Undo/redo with a bounded history. Every edit
regenerates the stitches, so the preview, the statistics and the exported file
never disagree.

**Validation.** Errors block export; warnings must be acknowledged. Each
message quotes the measured value:

> ERROR — Design width exceeds the 5" x 7" (130 x 180 mm) hoop by 0.42 inches
> (10.7 mm). Design is 140.7 mm wide; the hoop field is 130.0 mm.

**Status and readiness.** A Status tab reports digitization state, stitch count,
dimensions, thread cones vs colour stops, colour changes, validation status and
export status, plus the colour-stop list in sew order. A design that leaves a
colour and returns to it is labelled as such, because the operator's stop list
is then longer than their thread list.

**Export.** A real PES version 1 file: `#PES0001` header, a CEmbOne/CSewSeg
vector section and a PEC stitch block. After encoding, the bytes are decoded
again and compared against the design — signature, stitch count, colour blocks
and dimensions. If any check fails the file is not offered for download.

**Import.** PES and PEC files are read back into the application for viewing,
validation and re-export.

**Projects.** Saved to IndexedDB and to a versioned
`.norton-embroidery-project` file that carries the original artwork bytes
untouched. Older project versions are migrated forward; newer ones are refused
with a clear message.

## Honest limitations

These are real gaps, not things the UI pretends to do:

- **No text detection.** Lettering is digitized as ordinary shapes. The
  analysis panel says so rather than reporting a confidence it does not have.
- **Imported designs are not re-editable.** PES stores original shapes only in
  the version-specific vector section, which the reader does not parse. An
  imported design arrives as stitches: viewable, validatable, exportable, but
  its fill angle and density cannot be changed.
- **Only the Brother PEC 64-colour chart is bundled.** That table is defined by
  the PES format itself, so it is verifiable. Madeira and Isacord codes are
  *not* bundled and *not* guessed, because ordering the wrong cone costs more
  than looking one up. The thread system takes imported charts.
- **Photographs do not become good embroidery.** The analyser scores them
  poorly and says why; it does not silently produce a 60,000-stitch mess.
- **No pull compensation.** Fabric distortion is not modelled, so very fine
  work may need manual widening.
- **No automatic appliqué placement/tackdown sequence.**
- **Brother SE700 stitch-count limit is not enforced** because Brother does not
  publish one. A production warning is raised at high counts instead of
  inventing a hard threshold.
- **Desktop only.** Mobile layout is out of scope for this version.
- **Never sewn on hardware.** This is the big one. Every claim the application
  makes is about bytes, not thread. See the physical validation section above.

## Architecture

Layers do not reach upward; the UI never contains embroidery algorithms.

```
src/domain/       units, geometry, stitch, thread, machine/hoop, design, validation
src/processing/   image analysis, segmentation, tracing, digitizing,
                  stitch generation, order optimisation, validation
src/infra/        PES writer/reader, project format, IndexedDB storage
src/app/          orchestration: pipeline, editor operations, export, history
src/ui/           React workspace, canvas renderer, simulator, panels
```

`src/domain` and `src/processing` are free of DOM references, which is why the
whole pipeline is tested in Node. The browser only supplies pixels (via
`src/app/image-decode.ts`) and draws them back.

### Coordinate system

Internally the application uses the same system as the file format: 1 unit =
0.1 mm, x right, y **down**. Keeping the internal representation identical to
the export format removes a class of sign and scale bugs at export time.

## Adding a machine

Machine profiles are data. Add one in `src/domain/machine.ts` or register it at
runtime with `registerMachine()`:

```ts
registerMachine({
  id: 'my-machine',
  name: 'My Machine',
  manufacturer: 'Acme',
  hoops: [{ id: 'h1', name: '6" x 10"', width: mmToUnits(160), height: mmToUnits(260), safetyMargin: mmToUnits(2) }],
  defaultHoopId: 'h1',
  maxStitchCount: null,   // null when the manufacturer publishes no limit
  maxColorChanges: null,
  needles: 1,
  supportedFormats: ['PES'],
  maxStitchLength: mmToUnits(12.7),
  threadChartId: 'brother-pec',
  notes: 'Verify against the machine manual before production.',
});
```

Leave a limit `null` rather than guessing it: the validator skips checks it has
no real threshold for instead of inventing one.
