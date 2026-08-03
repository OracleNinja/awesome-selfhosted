# AI cost controls

Every AI dollar this product spends is measurable, budgeted and switchable-off.
This document is the audit: what calls a model, what deliberately does not, what
each call costs, and what happens when the money runs out.

Two things to know before the detail:

1. **There is exactly one AI call in the product.** It describes what a piece of
   artwork depicts. It cannot move a score, a stitch count or a price.
2. **The product is fully functional with AI switched off.** Analysis, scoring,
   vectorizing, digitizing, pricing, mockups, exports and production management
   are deterministic code and stay that way.

Costs quoted here are **estimates** computed from operator-configured rates in
`backend/config/ai_pricing.json`. They are not amounts billed by a provider.
Verify rates against the provider's own pricing page before relying on them.

---

## 1. Inventory of every AI call

The whole application was searched for external-model invocations — `openai`,
`anthropic`, `messages.create`, `chat.completions`, client constructions — across
the backend and the Next.js frontend. The result:

| # | Operation | File | Function | Endpoints | Tier | Model (default) | Input | Est. input tok | Est. output tok | Required? | Deterministic instead? | Cacheable | Downgradeable | Est. cost/call |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `vision_analysis` | `app/ai/gateway.py` | `run_vision_analysis` | `POST /designs/upload`, `POST /designs/{id}/analyze` | VISION | `claude-haiku-4-5` | Downscaled artwork + fixed prompt | ~1,300 | ≤700 (capped) | **No** — enrichment only | Partly: text *presence* is detectable by CV; naming the subject is not | **Yes**, tenant-scoped on artwork hash | Already on the cheapest vision-capable tier | **~$0.0048** worst case |

There is no second call. The frontend contains no provider SDK and no model
endpoint; it talks only to this API.

A regression test enforces this. `test_the_only_ai_call_site_is_the_gateway`
greps the entire `app/` tree for SDK call shapes and fails if one appears outside
`app/ai/providers.py`. A new AI feature cannot be merged without going through
the gateway, which means it cannot be merged without a budget.

### Cost per call, worked

At the configured rate for `claude-haiku-4-5` ($1.00 / $5.00 per MTok, as of
2026-06-24 — an estimate, see the rate file):

```
input   1,293 tok x $1.00/MTok = $0.00129
output    700 tok x $5.00/MTok = $0.00350   (ceiling; typical output is shorter)
                                 ---------
total per uncached analysis    = $0.00479
```

Output dominates, which is why the output ceiling is the tightest control in the
system.

---

## 2. What deliberately does not call a model

Anything code can do reliably, code does. This is enforced by
`DETERMINISTIC_BY_DESIGN` in `app/ai/operations.py` and surfaced at
`GET /api/v1/ai/inventory`.

| Capability | Implementation |
|---|---|
| Compatibility score | `app/services/analyzer.py::score_design` |
| Colour reduction | `app/embroidery/raster.py::quantize` (seeded k-means) |
| Thread matching | `app/embroidery/threads.py` (CIE L\*a\*b\* nearest match) |
| Stitch generation | `app/embroidery/digitizer.py` |
| Stitch counts | `app/embroidery/pattern.py` |
| Run-time estimates | `app/embroidery/pattern.py` |
| Thread consumption | `app/embroidery/reports.py` |
| **Pricing and quotes** | `app/services/pricing.py::calculate` |
| Quantity breaks | `app/services/pricing.py` |
| Machine compatibility | `app/services/machines.py` |
| File validation | `app/embroidery/raster.py`, `svg_parse.py` |
| Format conversion | `app/embroidery/writers/` |
| Mockup generation | `app/services/mockup.py` |
| Voice command parsing | `app/services/voice.py` (rule-based) |

`test_the_score_is_identical_with_and_without_ai` asserts the compatibility
score, verdict, palette and suggested fabric are byte-identical whether the AI
layer ran or not. A quote that moved because a model felt different today is not
a product.

---

## 3. Model routing

Business logic asks for a **tier**, never a model. `app/ai/routing.py` is the
only place a model id resolves, and the ids come from environment variables.

| Tier | Meaning | Anthropic default | OpenAI default |
|---|---|---|---|
| LOW | Bounded extraction, classification | `claude-haiku-4-5` | `gpt-4o-mini` |
| MEDIUM | Multi-step reasoning, modest context | `claude-sonnet-5` | `gpt-4o-mini` |
| HIGH | Strongest model, only where a weaker one demonstrably fails | `claude-opus-5` | `gpt-4o` |
| VISION | Vision-capable, used only when an image is genuinely needed | `claude-haiku-4-5` | `gpt-4o-mini` |

`vision_analysis` routes to VISION. It is bounded JSON extraction from one
image, so the cheapest vision-capable model is the right tool — not a
concession. Nothing currently routes to MEDIUM or HIGH; those tiers exist so the
next feature has somewhere correct to go.

`test_no_model_id_is_hard_coded_in_business_logic` greps every `.py` under
`app/` for `claude-*` and `gpt-*` and fails on any hit outside `core/config.py`.

---

## 4. Token budgets

Declared per operation in `app/ai/operations.py`. An operation that is not in
that registry cannot be called at all — `operation()` raises `UnknownOperation`.

| Bound | `vision_analysis` | Behaviour when hit |
|---|---|---|
| Max input tokens | 4,000 (beta mode) | Deterministic shrink, then clean rejection |
| Max output tokens | **700** | Hard `max_tokens` on the request |
| Timeout | 45 s | Attempt fails, classified as retryable |
| Max attempts | 2 (beta) / 4 (relaxed) | Give up, fall back to deterministic |
| Failure mode | `deterministic_fallback` | Analysis completes without the description |

**Output is never unbounded.** `max_output_tokens` is passed on every request
and asserted by `test_output_is_always_bounded`.

Oversized input is reduced deterministically before it is refused. The gateway
re-normalises at 768 → 512 → 384 → 256 px until the estimate fits; only if even
256 px will not fit does it reject with `input_too_large` **without calling the
provider**. Both paths are tested.

---

## 5. Context minimisation

The only thing sent to a provider is a downscaled, re-encoded copy of the
artwork plus a fixed prompt. Not sent: design name, filename, customer, order,
price, workspace id, user id, analysis history, or any conversation state. There
is no conversation — each call is a single stateless request.

`normalize_artwork` downscales to a 1024 px long edge, flattens transparency
onto white, and re-encodes through a fresh buffer, which drops EXIF, ICC
profiles and XMP. `test_normalisation_strips_metadata` plants a camera-maker tag
and asserts it does not survive.

**Measured effect** (image tokens ≈ pixels ÷ 750; the "before" column models the
provider's own server-side resize, so this is a like-for-like comparison, not a
comparison against an unrealistic uncapped baseline):

| Upload | Tokens @1568 px cap | Tokens @2576 px cap | Ours @1024 px | Saved vs 1568 | Saved vs 2576 |
|---|---|---|---|---|---|
| 1200×900 | 1,684 | 1,684 | 1,293 | 24% | 24% |
| 2000×1500 | 2,703 | 4,244 | 1,293 | 53% | 70% |
| 3000×2400 | 2,866 | 7,323 | 1,363 | 53% | 82% |
| 4000×3000 | 2,703 | 6,880 | 1,293 | 53% | 82% |
| 6000×4000 | 2,429 | 6,142 | 1,177 | 52% | 81% |

---

## 6. Caching

Tenant-scoped, stored in `ai_result_cache`, unique on
`(organization_id, cache_key)`.

The key is a SHA-256 over the operation, the **artwork hash**, the prompt
version, the model, and the request shape. Change the prompt or the model and old
entries stop matching rather than silently answering under new settings. Both
invalidation paths are tested.

**Tenant isolation is the point.** Every lookup filters on `organization_id`, and
the database enforces the uniqueness per tenant. Two workspaces that upload the
same logo each keep their own entry and each pay once. Artwork is customer
property; two shops must not be able to learn from each other that they share a
file. `test_the_cache_does_not_cross_tenants` asserts the second workspace still
reaches the provider. Unattributed calls (no workspace) are never cached — there
is no tenant to scope the entry to, and a shared bucket is exactly the leak this
design avoids.

Cache hits are written to the ledger, so the hit rate is a measured number
rather than an assumption.

---

## 7. Artwork hashing

SHA-256 over the **normalised** bytes, not the raw upload. That widens what
counts as the same artwork: a file re-saved at a different PNG compression
level, stripped of metadata, or flattened from RGBA hashes alike as long as the
pixels match. `test_hashing_the_normalised_bytes_widens_what_counts_as_the_same_file`
proves four differently-encoded files collapse to one hash.

Two renders of the same logo at *different resolutions* do not match — resampling
changes pixels, and this is an exact hash. That is deliberate. A perceptual hash
would catch that case and would also, eventually, collide two different logos and
serve one customer a description of another's artwork. An exact hash can only
miss, and a miss costs one API call.

`cache.previously_analyzed()` answers "has this workspace seen this file before",
scoped to the tenant.

---

## 8. Usage ledger

`ai_call_events`, append-only, **one row per attempt** plus one per cache hit,
budget block and kill-switch skip. If a row is missing, the call did not happen.

Recorded: workspace, user, operation, provider, model, tier, attempt number,
outcome, machine-readable reason, cache-hit flag, input/output/cache-read/
cache-write tokens, whether those counts are measured or estimated, estimated
cost, currency, latency, artwork hash, provider request id.

**Not recorded:** prompt text, artwork bytes, model output, customer names.
Cost control needs counts, money and outcomes. `test_the_ledger_stores_no_prompt_or_artwork`
asserts neither the prompt nor the model's answer appears in any stored row.

`estimated_cost_usd` is **NULL** for a model with no configured rate — never
zero. A zero would understate the bill and corrupt every average built on it.
The dashboard counts those separately as `unpriced_calls`.

A failed attempt records its *estimated* input tokens rather than zero: the
provider consumed them, and an optimistic meter is worse than an approximate one.

---

## 9. Cost budgets

Five scopes, two units each. Tokens always bind; dollars bind only for priced
models — which is why the token limits are the real safety net.

| Scope | Token limit | Dollar limit |
|---|---|---|
| Per request (input) | 4,000 | — |
| Per request (output) | 700 | — |
| Per user per day | 120,000 | unset |
| Per tenant per day | 400,000 | unset |
| Per tenant per month | 4,000,000 | $25 |
| Global per month | 60,000,000 | $400 |

On exhaustion the call **does not happen**. It is not queued, degraded or
retried later. The caller gets a clear message naming what still works, the
block is recorded in the ledger with zero tokens, and the analysis completes
deterministically. The application fails safe from a cost perspective: it spends
nothing.

The check is against spend *already recorded*, so a tenant with any headroom
gets one more call and the last call of a period may overshoot. That is the
correct trade — refusing on a projection denies service on an estimate — and the
overshoot is bounded by the per-request ceiling (~$0.005). Tested.

---

## 10. Retries

- Max attempts: **2** in beta cost mode, 4 relaxed, configurable.
- Exponential backoff: 1 s, 2 s, 4 s.
- **Only retryable errors are retried.** Rate limits, connection failures and
  5xx are retryable. Auth failures, permission denials, unknown models, invalid
  requests (including token-limit rejections) and refusals are not — retrying
  them burns money to arrive at the same error.
- Malformed JSON is not retried: the same prompt against the same image returns
  the same shape, so a retry pays twice for one answer.
- **Every attempt is a separate ledger row**, with its own tokens and cost.

**The provider SDKs' own auto-retry is switched off** (`max_retries=0`). Both
SDKs retry twice by default, which would turn one `create()` into three billed
requests the ledger never sees. Moving that decision into the gateway is what
makes "every retry is counted" true rather than aspirational. Asserted by
`test_sdk_auto_retry_is_disabled`.

---

## 11. Kill switches

| Switch | Effect |
|---|---|
| `AI_ENABLED=false` | **Global.** No AI call of any kind. |
| `VISION_ANALYSIS_ENABLED=false` | Just that operation; the rest of the layer is untouched. |
| `AI_PROVIDER=none` | No provider resolves. |
| `AI_CACHE_ENABLED=false` | Cache off (costs more; useful when debugging). |
| `AI_BUDGET_OVERRIDE=true` | An administrator deliberately spending past 100%. |

With AI off: analysis, scoring, colour reduction, thread matching, vectorizing,
digitizing, pricing, mockups, exports, production management and voice commands
all work identically. Only the artwork description is absent, and the analysis
says so in plain language rather than leaving a blank panel.

`test_the_product_still_works_with_ai_disabled` runs analysis → digitize → DST
export → quote with `AI_ENABLED=false` and asserts every stage produces real
output.

---

## 12. Beta cost mode

`AI_BETA_COST_MODE=true` by default, so a fresh deployment is cheap before
anyone has thought about budgets:

- Tighter output ceiling (700 vs 1,200 tokens) and input ceiling (4,000 vs 8,000).
- Fewer retries (2 vs 4).
- Caching on, tenant budgets on, full per-attempt tracking.
- Kill switch available at all times.

Beta users are not responsible for uncontrolled AI costs: the per-tenant monthly
ceiling is $25 / 4M tokens and the global ceiling stops everyone at $400 / 60M
tokens regardless of how many tenants there are.

---

## 13. Alerting

Thresholds at **50%, 80% and 100%** of every configured limit. 80% and above are
logged as warnings with the scope, the amounts and the workspace. All thresholds
are returned by `GET /api/v1/ai/costs` in the `alerts` block so they can be
wired to a pager without polling the logs.

At **100% AI calls stop**, unless an administrator sets `AI_BUDGET_OVERRIDE`,
which logs loudly on every subsequent call.

---

## 14. The cost dashboard

`GET /api/v1/ai/costs?period=day|week|month&scope=tenant|global`
(workspace admins and owners; `global` additionally requires the caller's email
in `AI_ADMIN_EMAILS`.)

The response has two halves that are never mixed:

- **`recorded`** — sums straight out of the ledger. Total calls by outcome,
  input/output/cache tokens, estimated cost, breakdowns by operation, model and
  tenant, failure and skip reasons, latency, unpriced call count, cache entries.
  Nothing inferred.
- **`calculated`** — ratios over `recorded`, each carrying the `formula` and the
  `denominator` it was computed from: cache hit rate, provider success rate,
  cost per design, cost per export, cost per provider call, average tokens per
  call.

A metric whose denominator is zero returns `null`, not `0.00`. Reporting "$0.00
per design" when no design exists is a fabricated financial result.
`test_a_calculated_metric_with_no_data_is_null_not_zero` enforces it.

`cost_per_successful_sewout` is present and permanently `null` with a note: the
product does not yet record sew-out confirmations, and it will not be inferred
from order status. It fills in when beta operators report completed runs.

`GET /api/v1/ai/inventory` returns the operation registry, the routing table,
the deterministic-by-design list, the kill-switch states and an explicit
statement of what is sent to providers.

---

## 15. Remaining cost risks

Stated plainly, because an audit that finds nothing is not an audit.

1. **Rates go stale.** `ai_pricing.json` is a hand-entered snapshot dated
   2026-06-24. If a provider changes prices, dollar figures drift until someone
   edits the file. Token figures never drift, which is why every scope has a
   token limit too. *Mitigation: token budgets bind independently; the file
   carries `as_of` and every payload is labelled an estimate.*
2. **OpenAI is unpriced.** No verified rate was available, so no rate was
   invented. OpenAI calls record real tokens with `cost = null` and appear in
   `unpriced_calls`. Dollar budgets do not bind for them. *Mitigation: run
   Anthropic, or add verified rates to the file.*
3. **Budgets are enforced per process, atomically only per transaction.** Two
   simultaneous requests can both read the same headroom and both proceed. The
   overshoot is bounded by one request per concurrent worker (~$0.005 each).
   *Mitigation for scale: move the check behind a row lock or Redis counter.*
4. **The overshoot allowance is by design.** A tenant at 99.9% gets one more
   full-cost call. Bounded by the per-request ceiling.
5. **Estimated input tokens on failure.** When a call fails before the provider
   reports usage, the ledger records our estimate. It is approximate — but it is
   recorded rather than dropped, and flagged `tokens_measured = false`.
6. **Cache misses on re-rendered artwork.** The exact hash misses when the same
   logo arrives at a different resolution. Deliberate: see §7. Costs one call.
7. **No batch API use.** Analyses are interactive, so the 50% batch discount is
   not applicable to the current call. It would apply to any future bulk
   re-analysis, which should be written against the Batch API from the start.
8. **Prompt caching is not used.** The prompt is ~244 tokens, below the minimum
   cacheable prefix for every current model, so there is nothing to cache. This
   becomes worth revisiting if the prompt grows past ~1,000 tokens.

---

## 16. Verification

60 tests in `backend/tests/test_ai_cost_controls.py`, all passing against a live
PostgreSQL 16 instance. No test makes a network call; the provider is stubbed at
the dispatch table, so the budget checks, ledger writes, cache, retry policy and
kill switches under test are the production ones.

Covered: token limits · output ceiling · timeout presence · deterministic input
reduction · clean rejection of unreducible input · budget limits at every scope ·
tenant budget exhaustion · global budget exhaustion · administrator override ·
tenant isolation of the cache · tenant isolation of duplicate detection · cache
hits · cache misses on prompt change · cache misses on model change · cache hits
recorded in the ledger · duplicate artwork detection · retry of retryable errors ·
no retry of auth/invalid/permission/not-found errors · retry ceiling · every
retry billed · SDK auto-retry disabled · AI disabled mode · per-operation
disable · product functional with AI off · score identical with and without AI ·
correct cost accounting from configured rates · null cost for unpriced models ·
no invented rates · no prompt or artwork in the ledger · no model ids in business
logic · single call site · dashboard recorded/calculated separation · null rather
than zero for empty denominators · reported attempt count matching the ledger ·
end-to-end repeat upload paying once.

Beyond the suite, the running API was driven end to end against live PostgreSQL:
the inventory and cost endpoints, an upload with a deliberately invalid key (the
Anthropic adapter reached the API, got a 401, classified it non-retryable and
stopped at one attempt), and a full `AI_ENABLED=false` pipeline — analyse →
vectorize → digitize → quote → DST/PES export with thread chart and production
notes → mockup → voice command — all of which produced real output with no model
involved.

Full suite: **166 passed**, from a schema built by the migrations alone.
