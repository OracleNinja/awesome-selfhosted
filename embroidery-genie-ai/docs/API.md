# API reference

Base path `/api/v1`. Interactive docs at `/docs`, schema at `/openapi.json`.

## Authentication

Every request carries a Supabase JWT:

```
Authorization: Bearer <access_token>
```

The backend verifies it against the project's signing key (HS256 shared secret,
or RS256/ES256 via the published JWKS) and checks audience and expiry. Nothing
trusts a header, a query parameter or a client-supplied user id — the subject
always comes out of a verified token.

`X-Organization-Id` selects the workspace when a user belongs to more than one.
Membership is verified on every request; a workspace you are not a member of
returns 403.

For local development, `ALLOW_DEV_AUTH=true` treats unauthenticated requests as
one fixed user. The app refuses to start with it enabled in production.

## Status codes

| Code | Meaning here |
|---|---|
| 402 | Your plan does not include this. The message names the tier that does. |
| 409 | The request is valid but the resource is in the wrong state (export before digitizing, an illegal order transition, overselling stock). |
| 422 | Bad input, or artwork the engine cannot use. The message says what to fix. |
| 503 | A server-side prerequisite is missing (e.g. no fonts installed for lettering). |

Errors are `{"detail": "...", "problems": [{"field": ..., "message": ...}]}`.

---

## Designs

| Method | Path | Notes |
|---|---|---|
| `GET` | `/designs` | `q`, `status`, `customer_id`, `limit`, `offset` |
| `POST` | `/designs/upload` | multipart: `file`, `name`, `analyze`, `use_ai` |
| `POST` | `/designs/text` | Lettering design |
| `POST` | `/designs` | Empty record |
| `GET` | `/designs/{id}` | |
| `GET` | `/designs/{id}/stitches` | Stitch stream for the simulator |
| `PATCH` | `/designs/{id}` | |
| `DELETE` | `/designs/{id}` | Also removes stored files |
| `POST` | `/designs/{id}/analyze` | Re-run the analyzer |
| `POST` | `/designs/{id}/vectorize` | Trace raster → vector layers |
| `POST` | `/designs/{id}/digitize` | Generate stitches |
| `POST` | `/designs/{id}/export` | Returns a ZIP |
| `POST` | `/designs/{id}/mockup` | Returns a PNG |
| `POST` | `/designs/{id}/duplicate` | |

### Digitize

```http
POST /api/v1/designs/{id}/digitize
{
  "fabric": "hat",
  "machine_id": "…",
  "target_width_mm": 115,
  "density_scale": 1.0,
  "fill_angle_deg": 45,
  "auto_underlay": true,
  "auto_satin": true,
  "max_colors": 6
}
```

Returns the stitch summary, per-colour thread chart, pre-flight `issues`,
per-object technique decisions, estimated run time and the resolved fabric and
machine.

### Export

```http
POST /api/v1/designs/{id}/export
{ "formats": ["dst", "pes", "jef"], "machine_id": "…", "order_id": "…" }
```

Returns `application/zip`:

```
design.dst
design.pes
design.jef
thread_chart.pdf        colour sequence with swatches and totals
production_notes.pdf    operator run sheet: setup, sequence, warnings
preview.png
preview.svg
manifest.json
```

Formats outside your plan return 402. Formats that cannot be written at all
(HUS) return 422 with the reason. Non-ASCII design names are encoded per
RFC 5987 in `Content-Disposition`.

---

## Studio reference

`GET /studio/reference` returns everything the UI needs in one call: fabrics,
export formats and their availability, fonts, thread catalogues, machine
presets, mockup templates, garment colours and placements.

Also: `GET /studio/fabrics`, `/studio/formats`, `/studio/fonts`,
`/studio/threads/{catalog}`, `GET /studio/estimate`,
`POST /studio/text/preview`, `POST /studio/text/render.png`.

---

## Production

Requires the Business plan; otherwise 402.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/orders` | `status`, `customer_id`, `overdue` |
| `GET` | `/orders/board` | Grouped by status for the kanban |
| `POST` | `/orders` | |
| `POST` | `/orders/{id}/status` | Validated transition + audit event |
| `GET/POST/PUT` | `/customers` | |
| `GET/POST/PUT` | `/products` | `POST /products/{id}/stock` to adjust |
| `GET/POST` | `/invoices` | `/send`, `/payments`, `/void` |

### Order state machine

```
quote ──▶ approved ──▶ digitizing ──▶ sewing ──▶ completed ──▶ delivered
  │           │            │            │            │
  └───────────┴────────────┴────────────┴─── cancelled
```

Illegal transitions return 409 listing what is allowed. Moving into `sewing`
consumes stock for any linked products — that is the point at which blanks
physically leave the shelf — and refuses with 409 if there is not enough.

---

## Pricing

```http
POST /api/v1/pricing/quote
{ "design_id": "…", "quantity": 24, "blank_cost": 4.25 }
```

Builds the true unit cost from machine time, thread, labour, blanks, waste and
overhead, then applies margin **on the selling price** (a 35% margin on a $10
cost is $15.38, not $13.50). Returns the full breakdown, quantity breaks and an
`assumptions` list so an operator can sanity-check every number — including the
implied "$ per 1,000 stitches" the industry quotes by habit.

Entirely deterministic. Pricing a job from a language model's intuition is how
shops lose money.

---

## Voice

```http
POST /api/v1/voice/command
{ "transcript": "Prepare a 50 shirt order" }
```

```json
{
  "intent": "create_order",
  "confidence": 0.95,
  "entities": { "quantity": 50, "fabric": "cotton_shirt" },
  "action": { "type": "navigate", "route": "/orders/new" },
  "reply": "Starting an order for 50 cotton shirts."
}
```

Rule-based, not model-based. These commands trigger real actions, and a parser
that is wrong 5% of the time is worse than no parser. Anything ambiguous returns
`intent: "unknown"` with suggestions rather than a guess.

---

## System

| Path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /ready` | Readiness — verifies the database; 503 when not ready |
| `GET /docs` | Swagger UI |

---

## Limits and abuse protection

Expensive endpoints are rate limited per **workspace** (the cost lands on the
workspace's plan, and a team shares one budget):

| Bucket | Endpoints | Limit |
|---|---|---|
| `upload` | `POST /designs/upload` | 20 / min |
| `digitize` | `/vectorize`, `/digitize`, `/designs/text` | 30 / min |
| `export` | `POST /designs/{id}/export` | 20 / min |
| `mockup` | `POST /designs/{id}/mockup` | 30 / min |
| `ai_analysis` | `POST /designs/{id}/analyze` | 15 / min |
| `preview` | `/studio/text/preview`, `/studio/text/render.png` | 60 / min |

Exceeding one returns **429** with a `Retry-After` header and a message naming
the limit.

> The limiter keeps its counters in process memory. That is correct for a
> single instance and deliberately simple; behind multiple replicas each
> process counts separately. Swap the store in `app/core/ratelimit.py` for
> Redis before scaling horizontally — the call sites do not change.

Other enforced limits:

- **Upload size** — 25 MB. Rejected on `Content-Length` before the body is
  buffered, then again while streaming. Returns 413.
- **Image dimensions** — 40 megapixels, checked on the header before any pixel
  is decoded, so a decompression bomb costs nothing.
- **Stitch budget** — 1,500,000 stitches, enforced *during* generation. A design
  past it fails with a 422 explaining how to reduce it, not an OOM.
