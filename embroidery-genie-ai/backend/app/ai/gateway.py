"""The AI gateway — the only way to reach an external model.

Every call in the product goes through :func:`run_vision_analysis`, and it does
the same eight things in the same order every time:

1. **Kill switches.** Global, then per-operation, then provider availability.
2. **Minimise.** Downscale and strip the artwork; nothing else is sent.
3. **Cache.** Tenant-scoped lookup on the artwork hash. A hit costs nothing and
   is still written to the ledger, so the hit rate is measurable.
4. **Bound the input.** Shrink deterministically to fit the token budget, or
   reject cleanly. Never send something unbounded.
5. **Budget.** Five scopes. Exhausted means the call does not happen.
6. **Call.** With a hard output ceiling and a timeout, retrying only errors that
   can succeed on a second try, with exponential backoff.
7. **Meter.** One ledger row per attempt, with tokens, cost and latency.
8. **Cache the answer** for the tenant that paid for it.

Every exit returns an :class:`AIOutcome` rather than raising. The caller — the
analyzer — treats a missing AI result as normal, because it is: the
deterministic score, the stitches, the price and the export do not depend on
any of this.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai import budget, cache, ledger, payload
from app.ai.operations import VISION_ANALYSIS, Operation, operation
from app.ai.providers import DISPATCH, ProviderError, api_key_for
from app.ai.routing import NoProviderAvailable, model_for, resolve_provider
from app.core.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIContext:
    """Who to bill and where to cache. Optional — see the note in the ledger."""

    db: Session
    organization_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


@dataclass
class AIOutcome:
    """What happened, in enough detail to explain it to a user or an auditor."""

    result: dict | None = None
    status: str = "skipped"          # ok | cached | skipped | blocked | failed
    reason: str | None = None        # machine-readable
    message: str = ""                # user-facing, only when it helps
    model: str | None = None
    provider: str | None = None
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None
    cached: bool = False
    artwork_hash: str | None = None
    duplicate_artwork: bool = False
    latency_ms: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status in ("ok", "cached")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "model": self.model,
            "provider": self.provider,
            "attempts": self.attempts,
            "cached": self.cached,
            "duplicate_artwork": self.duplicate_artwork,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "latency_ms": self.latency_ms,
            "artwork_hash": self.artwork_hash,
        }


VISION_PROMPT = """You are assisting an embroidery digitizer. Look at this artwork and \
answer ONLY with a JSON object, no prose, using exactly these keys:

{
  "subject": "one short phrase describing what the artwork shows",
  "artwork_type": "logo" | "lettering" | "illustration" | "photograph" | "pattern" | "mixed",
  "contains_text": true/false,
  "text_content": "the exact text you can read, or empty string",
  "smallest_text_words": ["words that appear notably smaller than the rest"],
  "has_gradients": true/false,
  "has_fine_lines": true/false,
  "has_outline_stroke": true/false,
  "dominant_shapes": ["circle", "shield", ...],
  "style_notes": "one sentence on the visual style",
  "embroidery_concerns": ["specific things that will be hard to stitch"]
}

Judge only what you can see. Do not estimate sizes in millimetres - you cannot \
know the finished size."""


def parse_json_response(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text.strip("`")
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ------------------------------------------------------------------- helpers
def _record(context: AIContext | None, call: ledger.CallRecord) -> float | None:
    """Write one ledger row, returning the estimated cost.

    A ledger failure must never take down the request it is measuring, but it
    must be loud: an unmetered call is exactly the thing this system exists to
    prevent.
    """
    if context is None:
        log.info(
            "AI call not metered (no database context): operation=%s outcome=%s",
            call.operation, call.outcome,
        )
        return None
    try:
        event = ledger.record(context.db, call)
        return float(event.estimated_cost_usd) if event.estimated_cost_usd is not None else None
    except Exception as exc:  # noqa: BLE001 - metering must not break the caller
        log.error("Failed to write the AI usage ledger (%s): %s", call.operation, exc)
        return None


def _skip(context, op: Operation, reason: str, message: str = "", **meta) -> AIOutcome:
    _record(
        context,
        ledger.CallRecord(
            operation=op.name,
            outcome=ledger.SKIPPED,
            reason=reason,
            organization_id=getattr(context, "organization_id", None),
            user_id=getattr(context, "user_id", None),
            meta=meta,
        ),
    )
    return AIOutcome(status="skipped", reason=reason, message=message)


# --------------------------------------------------------------------- entry
def run_vision_analysis(
    image_bytes: bytes,
    content_type: str = "image/png",
    context: AIContext | None = None,
) -> AIOutcome:
    """Describe artwork with a vision model, or explain why we did not."""
    op = operation(VISION_ANALYSIS)

    # 1 — kill switches, cheapest checks first.
    if not settings.ai_enabled:
        return _skip(context, op, "ai_disabled",
                     "AI features are switched off. Analysis, digitizing, pricing "
                     "and export are unaffected.")
    if not op.enabled():
        return _skip(context, op, "operation_disabled",
                     "AI artwork description is switched off for this deployment.")
    if not settings.ai_available():
        return _skip(context, op, "no_provider",
                     "No AI provider is configured; analysis is running on "
                     "computer vision only.")
    try:
        provider = resolve_provider()
        model = model_for(op.tier, provider)
    except NoProviderAvailable as exc:
        return _skip(context, op, "no_provider", str(exc))

    org_id = context.organization_id if context else None
    user_id = context.user_id if context else None

    # 2 — minimise. This is the single largest cost lever in the system: vision
    # input is priced by pixel area, and print-resolution artwork carries none
    # of the extra information the question needs.
    try:
        canonical = payload.normalize_artwork(image_bytes, content_type)
    except payload.ArtworkUnreadable as exc:
        return _skip(context, op, "unreadable_artwork", str(exc))

    duplicate = False
    if context is not None:
        try:
            duplicate = cache.previously_analyzed(context.db, org_id, canonical.artwork_hash)
        except Exception as exc:  # noqa: BLE001 - a cache miss is always safe
            log.warning("Duplicate-artwork check failed: %s", exc)

    prompt_tokens = payload.estimate_text_tokens(VISION_PROMPT)

    # 4 (before 3, so the key reflects what would actually be sent) — bound the
    # input deterministically, or refuse. Never send an unbounded payload.
    sent = payload.shrink_to_token_budget(canonical, prompt_tokens, op.max_input_tokens)
    if sent is None:
        return _skip(
            context, op, "input_too_large",
            "This artwork is too large to describe within the AI input budget. "
            "The deterministic analysis is unaffected.",
        )

    cache_key = cache.build_key(
        operation=op.name,
        artwork_hash=canonical.artwork_hash,
        prompt_version=op.prompt_version,
        model=model,
        request_shape={"px": f"{sent.width}x{sent.height}", "max_out": op.max_output_tokens},
    )

    # 3 — cache. Recorded in the ledger so the hit rate is a measured number.
    if op.cacheable and context is not None:
        try:
            hit = cache.lookup(context.db, org_id, cache_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("AI cache lookup failed, continuing to the provider: %s", exc)
            hit = None
        if hit is not None:
            _record(context, ledger.CallRecord(
                operation=op.name, outcome=ledger.CACHE_HIT, cache_hit=True,
                organization_id=org_id, user_id=user_id,
                provider=provider.value, model=hit.model, tier=op.tier.value,
                artwork_hash=canonical.artwork_hash,
                meta={"cache_age_hits": hit.hit_count},
            ))
            return AIOutcome(
                result=hit.result, status="cached", cached=True, model=hit.model,
                provider=provider.value, artwork_hash=canonical.artwork_hash,
                duplicate_artwork=True, meta={"cached_at": hit.created_at.isoformat()},
            )

    # 5 — budget.
    if context is not None:
        try:
            decision = budget.check(context.db, org_id, user_id)
        except Exception as exc:  # noqa: BLE001 - fail closed on an unreadable meter
            log.error("AI budget check failed; refusing to spend blind: %s", exc)
            return _skip(context, op, "budget_unavailable",
                         "AI usage could not be metered, so the AI description was "
                         "skipped. Everything else is unaffected.")
        if not decision.allowed:
            _record(context, ledger.CallRecord(
                operation=op.name, outcome=ledger.BLOCKED,
                reason=decision.reason_code, organization_id=org_id, user_id=user_id,
                provider=provider.value, model=model, tier=op.tier.value,
                artwork_hash=canonical.artwork_hash,
                meta={"scope": decision.blocking.name if decision.blocking else None},
            ))
            return AIOutcome(
                status="blocked", reason=decision.reason_code,
                message=decision.message, model=model, provider=provider.value,
                artwork_hash=canonical.artwork_hash, duplicate_artwork=duplicate,
            )

    # 6/7 — call, with every attempt metered.
    estimated_input = prompt_tokens + sent.estimated_image_tokens
    call_provider = DISPATCH[provider]
    api_key = api_key_for(provider)
    last_error: ProviderError | None = None
    total_cost = 0.0
    total_in = total_out = 0
    attempts_made = 0

    for attempt in range(1, op.max_attempts + 1):
        attempts_made = attempt
        started = time.perf_counter()
        try:
            response = call_provider(
                api_key=api_key,
                model=model,
                prompt=VISION_PROMPT,
                artwork=sent,
                max_output_tokens=op.max_output_tokens,
                timeout_seconds=op.timeout_seconds,
            )
        except ProviderError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            # A failed call still consumed input tokens at the provider. Record
            # the estimate rather than zero — an optimistic meter is worse than
            # an approximate one.
            cost = _record(context, ledger.CallRecord(
                operation=op.name, outcome=ledger.FAILURE, reason=exc.code,
                organization_id=org_id, user_id=user_id, provider=provider.value,
                model=model, tier=op.tier.value, attempt=attempt,
                usage=ledger.Usage(input_tokens=estimated_input, measured=False),
                latency_ms=elapsed, artwork_hash=canonical.artwork_hash,
                meta={"retryable": exc.retryable},
            ))
            total_cost += cost or 0.0
            total_in += estimated_input
            last_error = exc
            if not exc.retryable or attempt >= op.max_attempts:
                break
            delay = settings.ai_retry_backoff_seconds * (2 ** (attempt - 1))
            log.info(
                "AI attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, op.max_attempts, exc.code, delay,
            )
            time.sleep(delay)
            continue
        except Exception as exc:  # noqa: BLE001 - an adapter bug is not a crash
            elapsed = int((time.perf_counter() - started) * 1000)
            _record(context, ledger.CallRecord(
                operation=op.name, outcome=ledger.FAILURE, reason="adapter_error",
                organization_id=org_id, user_id=user_id, provider=provider.value,
                model=model, tier=op.tier.value, attempt=attempt,
                usage=ledger.Usage(input_tokens=estimated_input, measured=False),
                latency_ms=elapsed, artwork_hash=canonical.artwork_hash,
            ))
            log.exception("AI provider adapter raised an unclassified error")
            last_error = ProviderError(str(exc), code="adapter_error", retryable=False)
            break

        elapsed = int((time.perf_counter() - started) * 1000)
        usage = ledger.Usage(
            input_tokens=response.input_tokens or estimated_input,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            measured=response.measured,
        )
        parsed = parse_json_response(response.text)
        cost = _record(context, ledger.CallRecord(
            operation=op.name,
            outcome=ledger.SUCCESS if parsed else ledger.FAILURE,
            reason=None if parsed else "unparseable_response",
            organization_id=org_id, user_id=user_id, provider=provider.value,
            model=model, tier=op.tier.value, attempt=attempt, usage=usage,
            latency_ms=elapsed, artwork_hash=canonical.artwork_hash,
            request_id=response.request_id,
            meta={**sent.to_meta(), "stop_reason": response.stop_reason},
        ))
        total_cost += cost or 0.0
        total_in += usage.input_tokens
        total_out += usage.output_tokens

        if parsed is None:
            # Malformed JSON is not worth a retry: the same prompt against the
            # same image gets the same shape back, and we would be paying twice
            # for it.
            last_error = ProviderError(
                "The model did not return usable JSON.",
                code="unparseable_response", retryable=False,
            )
            break

        parsed["_provider"] = provider.value
        parsed["_model"] = model
        parsed["_prompt_version"] = op.prompt_version

        # 8 — cache the answer for the tenant that paid for it.
        if op.cacheable and context is not None:
            try:
                cache.store(
                    context.db, org_id, cache_key=cache_key, operation=op.name,
                    artwork_hash=canonical.artwork_hash,
                    prompt_version=op.prompt_version, model=model, result=parsed,
                )
            except Exception as exc:  # noqa: BLE001 - a cache write is not the answer
                log.warning("AI cache write failed: %s", exc)

        return AIOutcome(
            result=parsed, status="ok", model=model, provider=provider.value,
            attempts=attempt, input_tokens=total_in, output_tokens=total_out,
            estimated_cost_usd=round(total_cost, 8) if total_cost else None,
            artwork_hash=canonical.artwork_hash, duplicate_artwork=duplicate,
            latency_ms=elapsed, meta=sent.to_meta(),
        )

    reason = last_error.code if last_error else "unknown"
    log.warning(
        "AI vision analysis gave up after %d attempt(s) (%s): %s",
        attempts_made, reason, last_error,
    )
    return AIOutcome(
        status="failed", reason=reason,
        message="The AI artwork description is temporarily unavailable. "
                "The analysis, stitch file and price are unaffected.",
        # The attempts actually made, not the ceiling — a non-retryable error
        # stops at one, and reporting the ceiling would overstate the spend.
        model=model, provider=provider.value, attempts=attempts_made,
        input_tokens=total_in, output_tokens=total_out,
        estimated_cost_usd=round(total_cost, 8) if total_cost else None,
        artwork_hash=canonical.artwork_hash, duplicate_artwork=duplicate,
    )
