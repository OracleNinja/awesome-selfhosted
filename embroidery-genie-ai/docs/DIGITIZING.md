# How the digitizing engine works

This is the reference for the craft decisions encoded in
`backend/app/embroidery/`. It exists so that when the output looks wrong, you
can find the parameter responsible instead of guessing.

## Units

Everything internal is in **tenths of a millimetre** (0.1 mm). That is the native
resolution of DST, PES, JEF, EXP, VP3 and XXX, so a design can round-trip
between formats without accumulating error. Conversions live in `units.py` and
nothing else should do arithmetic on raw numbers.

## The pipeline

```
artwork ──▶ parse/trace ──▶ colour layers ──▶ per-shape plan ──▶ runs ──▶ assemble ──▶ pattern ──▶ writer
             svg_parse       digitizer          digitizer         fills      optimizer               writers
             raster                                               satin
                                                                  running
                                                                  underlay
```

### 1. Geometry in

**Vector** (`svg_parse.py`): paths with full command support (including arcs and
smooth curve continuations), rects, circles, ellipses, polygons, polylines,
lines, groups and nested transforms. Gradients and patterns collapse to nothing
stitchable, because thread cannot blend — the caller substitutes a flat colour.

**Raster** (`raster.py`):

1. Decode and EXIF-orient; downscale to a 1400 px working edge.
2. Background removal — alpha channel if present, otherwise a flood from the
   border with a colour tolerance. Only background *connected to the border* is
   removed; an interior area that happens to match the backdrop is part of the art.
3. Colour reduction — k-means in **L\*a\*b\***, so clusters follow perception
   rather than RGB arithmetic.
4. Morphological open/close to kill JPEG speckle that would otherwise become
   dozens of unstitchable 1 mm islands.
5. Contour tracing with hierarchy so holes stay holes, then Chaikin smoothing and
   Douglas-Peucker simplification.

### 2. Layer resolution

Colours are collected in paint order. **Later colours knock themselves out of
the layers underneath** — otherwise you pay for stitches nobody will ever see
and the design goes stiff.

If a colour budget is set, the least-used colours merge into their nearest
neighbour (perceptually) until the design fits.

### 3. Per-shape planning (`digitizer.py::_plan_area`)

For each closed area, `4 × area / perimeter` estimates the average width — exact
for a long thin rectangle, which is the shape satin is used on.

| Average width | Treatment |
|---|---|
| < 0.8 mm | Triple running stitch down the spine. Nothing narrower holds thread. |
| ≤ fabric's satin limit | Satin column |
| otherwise | Tatami fill |
| area < 12 mm² | Running outline — a fill this small costs more in trims than it delivers |

Fill angle rotates 30° per colour so adjacent colours read as separate shapes,
and never lands on 0° or 90°, which show needle lines on most fabrics.

### 4. Underlay (`underlay.py`)

Runs **before** the top stitching, always, and roughly **perpendicular** to it so
the two layers lock together instead of sinking into the same needle holes.

| Type | What it does |
|---|---|
| `center_run` | Single run down the spine — stabilises, minimal stitch cost |
| `edge_run` | Run just inside the outline — holds the edge crisp |
| `zigzag` | Open zigzag across the shape — lifts pile, adds body |
| `tatami` | Very open fill — foundation under large solid areas |

Satin columns get a light foundation only; a full tatami underlay under a satin
is wasted thread.

### 5. Fill generation (`fills.py`)

The row-grouping step is what separates a usable fill from a jump-stitch mess:

1. Rotate the polygon so rows are horizontal.
2. Scan horizontal lines at the fabric's row spacing, clipping to the polygon.
3. **Union-find** segments that overlap between adjacent rows into *sections*.
4. Sew each section boustrophedon; travel between sections.

A doughnut or a letter "E" then sews as a handful of clean passes instead of
hundreds of jumps.

Needle penetrations are **staggered** — each row's first stitch is phase-shifted
by `(row % 4) / 4` of the stitch length — so no visible split line develops down
the middle of the fill.

### 6. Satin generation (`satin.py`)

A satin column is two rails. Either:

- **From a centreline** — offset to both sides by half the width (text, strokes).
- **From a narrow closed contour** — find the vertex farthest from the centroid,
  then the vertex farthest from *that*; those two points are the ends of the
  column, and the ring splits into two rails between them. Robust for the
  elongated shapes satin is actually used on.

Both rails are resampled to the same point count stepped along the *longer*
rail, so density stays even through curves. Legs longer than the machine limit
are split into anchored sub-stitches so they cannot snag.

**Pull compensation** widens the column perpendicular to the stitch direction:
thread tension pulls fabric inward as it sews, so a 3 mm column digitised at
exactly 3 mm finishes narrower than drawn.

### 7. Assembly (`optimizer.py`)

- Runs are grouped by thread colour to minimise colour changes, then chained
  greedily to minimise travel — within each `order_hint` band, so underlay never
  jumps ahead of its own top stitching.
- Travel over 25 mm → tie-off, trim, jump. Travel over 6 mm → jump. Under that,
  a plain stitch.
- **Tie-offs retrace the run that just finished.** Anchoring anywhere else drags
  the needle across the design before the trim, which is a guaranteed thread
  break. (This was a real bug caught by the test asserting no stitch exceeds the
  machine limit.)
- Every stitch is clamped to the machine maximum and degenerate sub-0.6 mm
  stitches are dropped.

### 8. Pre-flight

Checked against the selected machine and reported, never silently "fixed":

| Code | Level | Meaning |
|---|---|---|
| `hoop_width` / `hoop_height` | error | Larger than the hoop's sewing field |
| `stitch_limit` | error | Over the machine's stitch capacity |
| `empty` | error | Nothing was generated |
| `color_limit` | warning | More colours than needles |
| `high_density` | warning | Above ~180 stitches/cm² |
| `small_detail` | warning | Elements below a third of the fabric's minimum letter height |
| `wide_satin` | warning | Columns past the fabric's satin limit |
| `many_trims` | info | Consolidating detached shapes would cut run time |
| `low_coverage` | info | Fabric will show through |

For reference, a standard tatami fill (0.4 mm rows, 4 mm stitches) lands near
**60 penetrations per cm²**.

## Fabric profiles

Every craft parameter comes from the profile, not from a global default.

| Fabric | Rows | Satin | Compensation | Underlay | Speed | Min letters |
|---|---|---|---|---|---|---|
| Cotton shirt | 0.40 mm | 0.35 mm | 0.15 mm | edge run | 100% | 4.5 mm |
| Polyester shirt | 0.45 mm | 0.38 mm | 0.25 mm | centre + edge | 85% | 5 mm |
| Fleece hoodie | 0.42 mm | 0.36 mm | 0.30 mm | centre + zigzag + edge | 85% | 6 mm |
| Structured cap | 0.38 mm | 0.35 mm | 0.30 mm | centre + zigzag | 65% | 6 mm |
| Knit beanie | 0.45 mm | 0.40 mm | 0.45 mm | centre + zigzag + edge | 60% | 8 mm |
| Leather patch | 0.50 mm | 0.45 mm | 0.05 mm | none | 55% | 5 mm |
| Canvas | 0.42 mm | 0.36 mm | 0.10 mm | edge run | 80% | 4.5 mm |
| Terry towel | 0.38 mm | 0.32 mm | 0.35 mm | centre + zigzag + edge | 70% | 8 mm |

The pattern: stable wovens get light underlay and minimal compensation;
stretchy knits and pile fabrics get heavy underlay, generous compensation, a
slower head and larger minimum lettering.

Leather is the exception in the other direction — every penetration is
permanent, so underlay is off entirely and density is opened up. Perforation
weakens the substrate.

## Run time estimation

```
minutes = stitches / (machine_spm × fabric_speed_factor)
        + colour_changes × 0.35      # ~21 s each, including restart
        + trims × 0.02               # ~1.2 s each
```

The overheads are what quotes normally forget, and they dominate on designs with
many colours or many detached elements.
