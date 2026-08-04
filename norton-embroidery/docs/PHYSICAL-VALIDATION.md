# Physical machine validation — Brother SE700

**Status: NOT PERFORMED.** No design produced by this application has been
confirmed to sew correctly on a physical Brother SE700. Nothing in the software
can change that status. Only a completed stitch-out, recorded on a worksheet by
an operator, can.

This document is the procedure for producing that evidence.

---

## What is already established, and what is not

| Tier | What it means | Established by |
|---|---|---|
| **Software validated** | The design passes this application's rules for the target machine: it fits the hoop, stitch lengths are legal, coordinates are finite, the colour sequence resolves. | The validator, run on every edit. Visible in the Status tab. |
| **PES format validated** | The exported bytes decode back to the same stitch count, colour order and dimensions they were generated from, and `pyembroidery` — an independent implementation — reads them the same way. | Post-export verification in the app; `npm test`. |
| **Machine validated** | A physical machine loaded the file, sewed it, and the result was acceptable on fabric. | **Nothing yet. This procedure.** |

The first two tiers are about *bytes*. The third is about *thread, fabric,
tension and a moving needle*. A file can be perfectly well-formed and still sew
badly, so the tiers are reported separately and are never collapsed into a
single "ready" claim.

---

## Before you start

You need:

- A Brother SE700 with a current firmware version.
- A USB flash drive formatted FAT32. The SE700 reads designs from USB.
- The 4"×4" hoop and the 5"×7" hoop.
- Scrap fabric of the type the job will actually run on. Mid-weight woven
  cotton is the least demanding starting point; knits and piqué are harder and
  should be tested separately.
- Cut-away stabiliser for knits, tear-away for stable wovens.
- 40-weight polyester or rayon embroidery thread in the colours the design
  calls for, plus bobbin thread.
- A fresh 75/11 embroidery needle.
- A ruler or calipers that reads millimetres.
- A printed copy of the stitch-out worksheet (Status tab → **Save stitch-out
  worksheet**) and the readiness report (**Save readiness report**).

Do not run the first tests on a customer garment.

---

## The test set

Run the six controlled designs in `fixtures/` in order. They are ordered by how
much they stress the digitizer, so a failure tells you *which* part of the
engine is at fault rather than just "it did not work".

| # | Fixture | What it proves if it sews cleanly |
|---|---|---|
| 1 | `01-single-color-square` | Basic fill, one colour, no colour change. Registration and density baseline. |
| 2 | `02-two-color-logo` | One colour change. The machine stops, you re-thread, it resumes in register. |
| 3 | `03-multi-color-logo` | Several colour changes and colour grouping. |
| 4 | `04-holes-and-cutouts` | Fill with holes: the needle must not stitch across the cut-outs. |
| 5 | `05-narrow-satin` | Satin columns hold at width, ends are square, columns do not pull into a hole. |
| 6 | `06-detailed-logo` | Small detail, higher stitch count, more trims. The realistic worst case. |

Regenerate these at any time:

```bash
npm run fixtures:build     # writes fixtures/out/<name>/{artwork.png,preview.png,design.pes,report.json}
```

Each output folder gives you the four things to compare:

1. `artwork.png` — what the customer supplied.
2. `preview.png` — what the software says it will sew.
3. `design.pes` — the file the machine reads.
4. the physical stitch-out — what actually came out.

---

## Procedure, per design

### 1. Load

Copy `design.pes` to the USB drive root. Insert it in the SE700 and open the
design from the USB menu.

**Record on the worksheet:**

- Did the machine list the file at all? A file the machine will not list is a
  format failure — stop and report it, because that is a software defect, not
  an operator problem.
- The stitch count the machine displays.
- The number of colour stops the machine displays.
- The design size the machine displays.

Compare each against the "WHAT THE SOFTWARE PREDICTS" block on the worksheet.
A mismatch here is the single most valuable result this whole procedure can
produce: it means the encoder and the machine disagree, and it should be
reported before anything is sewn.

### 2. Hoop

Hoop the scrap fabric with stabiliser. Keep it drum-tight but do not stretch
knits. Use the hoop the worksheet names — the design was validated against that
hoop's field, and a smaller one will not fit.

### 3. Position

Use the machine's positioning preview to confirm the design sits inside the
hoop with clearance on all sides. The software leaves a 2 mm safety margin and
warns when a design comes within it; confirm that margin is really there.

### 4. Sew

Sew the design at a moderate speed for the first run. Watch it, do not walk
away. Stop immediately if you see the needle deflecting, thread nesting under
the fabric, or the fabric lifting with the needle.

**Record:** thread breaks, any stop, anything that needed intervention.

### 5. Inspect

With the sample still hooped, then again unhooped:

- **Registration.** Do the colour areas meet? Gaps between adjacent colours
  point at the digitizer, not the machine — note the size of the gap in mm.
- **Density.** Is the fabric perforated or stiff as card? Is the fill see-through?
- **Satin.** Do the columns have clean edges and square ends, or have narrow
  ones pulled into a slit?
- **Small detail.** Is anything the software warned about actually illegible?
- **Puckering.** Does the fabric ripple around the design once unhooped?

### 6. Measure

Measure the finished width and height with a ruler and write them down. Compare
to the predicted size. A consistent difference across all six designs points at
a scale error in the encoder and is a software bug. A small difference on
stretchy fabric is normal fabric distortion.

### 7. Record the verdict

Complete the RESULT block on the worksheet and sign it. Keep the sheet stapled
to the sample.

---

## What each failure mode means

| What you see | Most likely cause | Where to look |
|---|---|---|
| Machine will not list the file | PES header or PEC block malformed | `src/infra/pes/pes-writer.ts`, `pec.ts` |
| Machine shows a different stitch count | Encoder dropping or adding stitches | `pecEncode`, and the post-export verification |
| Design sews mirrored or rotated | Coordinate sign convention | `src/domain/units.ts` — internal space is y-down to match the format |
| Design sews at the wrong size | Unit scale error | `mmToUnits` / `UNITS_PER_MM` |
| Wrong colour at a stop | Block-to-thread mapping | `src/domain/stitch-sequence.ts` — this is exactly the bug `colorSequence` exists to prevent |
| Gaps between adjacent colour areas | No pull compensation (a known, documented gap) | `src/processing/digitize/digitizer.ts` |
| Fabric perforated | Fill density too tight for the fabric | Lower density per object, or use a softer stabiliser |
| Narrow satin pulls into a slit | Column below the reliable minimum | The validator warns at 1.0 mm; heed it |
| Excessive thread breaks | Density, needle, tension, or too many short stitches | Check the reported minimum stitch length first |
| Puckering | Stabiliser or hooping, usually not the file | Re-hoop before blaming the digitizer |

---

## Reporting a result back into the software

If a stitch-out reveals a defect:

1. Write down the fixture, the exact symptom, and the measurement.
2. Reproduce it in software if you can — the fixture regression suite
   (`npm test`) runs the same six designs through the pipeline and asserts
   stable metrics, so a change in behaviour shows up there.
3. Fix the engine, not the fixture. The golden values in
   `fixtures/golden.json` are a record of current behaviour, not a target to be
   edited into agreement.
4. Re-run `npm test`, `npm run lint`, `npm run typecheck`, `npm run build` and
   the browser smoke test before re-testing on the machine.

---

## When can this application claim SE700 compatibility?

When all six fixtures have completed worksheets showing:

- the machine listed and loaded every file,
- the machine's stitch count and colour stop count matched the prediction
  exactly on every design,
- measured size was within 1 mm of prediction on stable woven fabric,
- and the result was marked **acceptable** by the operator.

Until then the application reports **HARDWARE NOT VERIFIED**, and it should.
Do not remove that banner to make a demo look better.
