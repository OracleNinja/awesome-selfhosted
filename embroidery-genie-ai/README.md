# Embroidery Genie AI

AI-powered embroidery digitizing and production management. Upload artwork, get
machine-ready stitch files, thread charts, production paperwork and pricing.

**Idea → embroidery file → production order**, in one workspace.

> This is a standalone product. It lives in its own directory and shares no code,
> data or build with anything else in this repository.

---

## What it actually does

Most "auto-digitizers" trace an outline and dump a fill into it. This one makes
the decisions a human digitizer makes:

| Decision | How it is made |
|---|---|
| Satin or fill? | Average column width vs the fabric's satin limit (~10 mm on cotton, 8 mm on caps) |
| How dense? | Fabric profile, opened slightly on very large areas so they do not board up |
| What underlay? | Per-fabric recipe (edge run / centre run / zigzag / tatami), laid perpendicular to the top stitching |
| How much compensation? | Scaled to how much the fabric moves — 0.15 mm on cotton, 0.45 mm on a beanie |
| Stitch length in a fill? | ~4 mm (3.5 mm on caps) — a craft choice, not the 12 mm machine limit |
| Sew order? | Colours consolidated, travel minimised, underlay always before top stitching |
| Where do trims go? | Travel over 25 mm, with a tie-off retracing the run that just finished |

The engine works in tenths of a millimetre throughout, which is the native
resolution of every commercial format, so nothing accumulates rounding error on
the way to the machine.

## Modules

| # | Module | Status |
|---|---|---|
| 1 | Authentication, workspaces, plan tiers | Supabase JWT (HS256 + JWKS), just-in-time provisioning |
| 2 | AI design analyzer | CV metrology + optional OpenAI/Claude vision |
| 3 | Vectorization engine | Background removal, Lab k-means, contour tracing, optional potrace |
| 4 | Digitizing engine | Satin, tatami fill, running/bean, 4 underlay types, 8 fabric profiles |
| 5 | Machine profiles | 13 presets (Tajima, Barudan, Ricoma, Melco, Brother, Janome, Viking, Singer) + pre-flight validation |
| 6 | Export engine | DST, PES, JEF, EXP, VP3, XXX, U01, PEC + thread chart, run sheet, preview, manifest |
| 7 | 3D stitch simulator | Canvas playback with thread shading, zoom/rotate/tilt, per-colour layers |
| 8 | Production management | Customers, orders (validated state machine), inventory, invoicing |
| 9 | AI pricing assistant | Machine time, thread, labour, blanks, waste, overhead → wholesale/retail with quantity breaks |
| 10 | Customer mockups | 8 garment templates at true physical scale |
| 11 | Voice command mode | Web Speech API + deterministic intent parser |

### A note on the analyzer

The analyzer has two layers, deliberately separated:

- **Deterministic** (always runs): colour count, edge density, minimum feature
  width via distance transform, small-part count. This produces the
  compatibility score. It needs no API key, no network, and costs nothing.
- **Semantic** (optional): a vision model describes *what* the artwork is and
  whether it contains small lettering.

Nothing the model says can move the score. Models are good at description and
bad at metrology, and a quote that changes because a language model felt
different today is not a product.

### A note on export formats

Embroidery formats are undocumented binary blobs reverse-engineered by the
community. Two are stable and simple enough to own outright, and they are
implemented in `app/embroidery/writers/`:

- **DST** (Tajima) — the universal commercial format. Verified by round-tripping
  through an independent decoder in the test suite.
- **EXP** (Melco).

PES, JEF, VP3, XXX, U01 and PEC carry version-specific headers, embedded preview
bitmaps and vendor colour tables. Those are written through
[`pyembroidery`](https://github.com/EmbroidePy/pyembroidery), a mature
MIT-licensed implementation, rather than re-derived from memory into files that
*look* right and fail on a machine at 2 a.m.

**HUS is import-only.** Its stitch block uses a proprietary compression with no
open writer. The API says so explicitly and points at VP3, which is what modern
Viking and Pfaff machines want anyway — rather than silently emitting a broken
file.

---

## Quick start

### Docker (everything at once)

```bash
docker compose up --build
```

- App: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>

Migrations run automatically. `ALLOW_DEV_AUTH=true` in the compose file lets you
use the app without setting up Supabase.

### Local development

**Backend** (Python 3.11+, PostgreSQL 14+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                    # then edit DATABASE_URL

createdb embroidery_genie
alembic upgrade head
python scripts/seed.py                  # optional demo data

uvicorn app.main:app --reload
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

`next.config.mjs` proxies `/api/v1/*` to `BACKEND_URL`, so the browser stays on
one origin and uploads never hit a CORS preflight.

### System packages

Two optional packages meaningfully improve output:

```bash
# Debian/Ubuntu
apt-get install potrace fonts-dejavu-core
```

- **potrace** — better curve fitting when tracing high-contrast art. Without it
  the OpenCV contour path handles everything; quality is slightly lower on
  smooth curves.
- **fonts-dejavu-core** — required for text/lettering designs. The API reports
  `fonts_healthy: false` and the UI explains the fix if no font is found.

---

## Testing

```bash
cd backend

# Engine + hardening tests — no database needed
pytest tests/test_engine.py tests/test_hardening.py

# Full suite including API integration
createdb embroidery_genie_test
TEST_DATABASE_URL=postgresql+psycopg://localhost/embroidery_genie_test pytest
```

106 tests: stitch geometry and density, format writers, an independent DST
round-trip, the full upload → analyze → digitize → export path, tenancy, plan
quotas, the order state machine, stock consumption, invoicing, voice parsing,
plus a hardening suite covering decompression bombs, XXE and entity expansion,
determinism of scoring/pricing/digitizing, rate limiting, path traversal,
cross-workspace access and AI-layer failure.

API tests skip cleanly when `TEST_DATABASE_URL` is unset, so `pytest` stays
useful on a laptop with no database running.

## Operational limits

Enforced, and worth knowing before you tune them:

| Limit | Value | Where |
|---|---|---|
| Upload size | 25 MB, checked on Content-Length then streamed | `MAX_UPLOAD_MB` |
| Decoded image | 40 megapixels, checked on the header before decoding | `raster.MAX_DECODED_PIXELS` |
| Stitches per design | 1,500,000, enforced during generation | `AssemblyParams.stitch_budget` |
| Digitize / upload | 30 / 20 per minute per workspace | `core/ratelimit.py` |
| Export / mockup | 20 / 30 per minute per workspace | `core/ratelimit.py` |
| AI vision call | 45 s timeout, failure degrades to CV-only | `AI_TIMEOUT_SECONDS` |

The rate limiter is **in-process**. It protects one worker; behind N replicas
the effective limit is N x the value. Point it at Redis before scaling out.

---

## Architecture

```
embroidery-genie-ai/
├── backend/
│   ├── app/
│   │   ├── embroidery/          # the engine — imports nothing from the web layer
│   │   │   ├── units.py         # 0.1 mm internal units
│   │   │   ├── pattern.py       # format-neutral stitch model
│   │   │   ├── geometry.py      # polygon ops, simplify, resample
│   │   │   ├── fills.py         # tatami with row-section grouping + stagger
│   │   │   ├── satin.py         # columns from centrelines or narrow contours
│   │   │   ├── running.py       # running / bean / travel
│   │   │   ├── underlay.py      # edge run, centre run, zigzag, tatami
│   │   │   ├── fabrics.py       # 8 profiles driving every craft parameter
│   │   │   ├── threads.py       # Lab colour matching, CSV chart import
│   │   │   ├── svg_parse.py     # paths, arcs, beziers, transforms, groups
│   │   │   ├── raster.py        # background removal, k-means, tracing
│   │   │   ├── text.py          # lettering → outlines → satin
│   │   │   ├── digitizer.py     # orchestration + craft decisions
│   │   │   ├── optimizer.py     # order, ties, trims, clamping, pre-flight
│   │   │   ├── render.py        # PNG / SVG / stitch stream
│   │   │   ├── reports.py       # thread chart + run sheet PDFs
│   │   │   ├── package.py       # export ZIP
│   │   │   └── writers/         # DST + EXP native; the rest via pyembroidery
│   │   ├── api/v1/routes/       # 12 routers
│   │   ├── services/            # analyzer, pricing, mockup, voice, storage, plans
│   │   ├── models/              # SQLAlchemy — multi-tenant from day one
│   │   └── core/                # config, Supabase JWT, dependencies
│   ├── migrations/              # Alembic
│   └── tests/
└── frontend/
    ├── app/(app)/               # dashboard, studio, digitizer, orders, …
    ├── components/studio/       # the stitch simulator
    ├── components/ui/           # design system primitives
    └── lib/                     # typed API client, Supabase, formatting
```

The engine imports nothing from the web layer, so it works as a library, from a
worker, or from a CLI:

```python
from app.embroidery import digitize_svg, DigitizeOptions, write

result = digitize_svg(svg_text, DigitizeOptions(fabric="hat", target_width_mm=115))
open("logo.dst", "wb").write(write(result.pattern, "dst"))

print(result.pattern.summary())   # stitches, colours, size, thread length
print(result.issues)              # pre-flight warnings
```

### Database

Ten tables, multi-tenant from the schema up: every business record hangs off an
`organization`, so team accounts and enterprise customers need no migration
later. `users.id` **is** the Supabase `auth.users.id`, so there is no mapping
table and no signup webhook to keep in sync — the first authenticated request
provisions the user, a workspace and a free subscription.

Stitch data is deliberately **not** stored as a blob. The settings plus a
versioned engine reproduce it exactly, so every design stays re-renderable after
an engine improvement.

---

## Deployment

### Environment

The backend refuses to start in production if the configuration is unsafe —
`ALLOW_DEV_AUTH` still on, no way to verify JWTs, or `DATABASE_URL` still
pointing at localhost. See `backend/.env.example` for every variable.

### Supabase

1. Create a project; enable Email and Google providers.
2. Create a **private** storage bucket named `designs`.
3. Backend: set `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
   `STORAGE_BACKEND=supabase`, and `ALLOW_DEV_AUTH=false`.
   Legacy HS256 projects also need `SUPABASE_JWT_SECRET`; asymmetric projects
   fetch the JWKS from `SUPABASE_URL` automatically.
4. Frontend: set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

Access control is enforced in the API, which is the only thing holding the
service key. The client never sees it.

### Migrations

```bash
alembic upgrade head                              # apply
alembic revision --autogenerate -m "description"  # after changing models
alembic downgrade -1                              # roll back one
```

### Scaling notes

Digitizing is CPU-bound and runs inline today, which is fine up to roughly a
design every few seconds per worker. When that stops being true, `digitize_design`
is already a pure function of (artwork, settings) — move it behind a queue and
have the route poll `Design.status`. Nothing else has to change.

The API is stateless; run as many replicas as you like behind a load balancer.

---

## Built for what comes next

The schema and module boundaries already accommodate:

- **Team accounts** — `organizations` / `memberships` / roles exist and are enforced
- **Enterprise** — per-workspace subscriptions, seats and usage metering
- **Marketplace** — designs are self-contained records with files and licensing metadata
- **Machine connection** — `machines.connection` (JSONB) is reserved for it
- **Cloud sewing queue** — orders carry an assigned machine and a status machine
- **Mobile** — the API is the whole product surface; the web app is one client
- **Trained digitizing model** — every design stores its artwork, settings and
  the resulting stitch data, which is exactly the training pair a model needs

---

## Licence and data notes

Thread catalogues from Madeira, Isacord and Robison-Anton are licensed data, so
they are not bundled. A 64-colour house catalogue ships instead, and any
manufacturer chart can be imported as CSV (`code,name,hex`). Colour matching
runs in CIE L\*a\*b\* so the nearest thread is the nearest *perceptually*, not
the nearest in RGB arithmetic.

Always sew a test piece on the same blank and stabiliser before running
production quantities. No digitizer, human or otherwise, gets every design right
without a sew-out.
