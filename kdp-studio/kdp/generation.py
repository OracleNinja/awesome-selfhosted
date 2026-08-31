"""Running generation, with attempts.

The loop is small and its rules are strict:

* One request per page, from that page's own prompt.
* Every attempt is recorded — successes, failures and rejections alike.
* A failure records why and stops. It never substitutes a placeholder, never
  reuses a neighbouring page, and never silently drops the page. A missing page
  has to stay missing so QA can see it.
* **A page is only regenerated when its last attempt was rejected or failed.**
  An approved page is finished. A *pending* page has been generated and is
  waiting on QA's verdict — regenerating it would pay a metered provider twice
  for the same page and throw away the version QA is about to look at.
* **Overriding that needs a decision, not a flag.** ``regenerate`` takes the
  page ids to redo. There is deliberately no "redo everything" switch: naming
  the pages is what makes the spend deliberate, and it is the difference
  between a considered re-run and a fat-fingered second invoice.

Retries are bounded per page rather than globally, so one hopeless page cannot
exhaust the budget for the rest of the book.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from .models.manifest import BookManifest
from .models.prompts import AssetPrompt
from .models.provenance import (
    AIRole,
    AssetKind,
    AssetProvenance,
    AssetStatus,
    Transformation,
    content_hash,
)
from .providers.base import (
    GenerationRequest,
    ImageProvider,
    ProviderUnavailable,
)

#: How many times one page may be attempted before the runner gives up on it.
DEFAULT_MAX_ATTEMPTS = 3

#: Requested pixel size when a prompt does not state one: the live area of an
#: 8.5x11 interior with no bleed, 7.875 x 10.5 in, at 320 DPI. It has to be a
#: shape the image API can actually be asked for — the previous 2400x3000 was
#: 4:5, which is not in either accepted set, so a prompt that fell back to it
#: would have been refused by ``build_request`` below.
DEFAULT_WIDTH = 2520
DEFAULT_HEIGHT = 3360

#: The aspect ratios a Google image request may name, and what each one means
#: as width/height. Deliberately the *intersection* of the two sets documented
#: in google-genai 2.20.0: ``GenerateImagesConfig.aspect_ratio`` lists "1:1",
#: "3:4", "4:3", "9:16" and "16:9", while ``ImageConfig.aspect_ratio`` adds
#: "2:3", "3:2" and "21:9". Taking the intersection means the value is legal
#: whichever of those two request shapes the provider ends up sending.
#:
#: This is checked rather than passed through because an unrecognised ratio is
#: not an error you get to see. The server ignores it, returns its default
#: square, and bills for it — which is what a page requested at 2520x3360
#: coming back square is consistent with.
SUPPORTED_ASPECT_RATIOS: Final[tuple[tuple[str, float], ...]] = (
    ("1:1", 1.0),
    ("3:4", 3 / 4),
    ("4:3", 4 / 3),
    ("9:16", 9 / 16),
    ("16:9", 16 / 9),
)

#: How far a page's own proportions may sit from the nearest supported ratio,
#: as a fraction of that ratio. A live area is trim minus margins, so it rarely
#: lands on a clean ratio; 2% is about the most that can be absorbed without the
#: art being visibly cropped or stretched on the page.
ASPECT_RATIO_TOLERANCE: Final[float] = 0.02

#: Named output sizes and the longest edge each one produces, in pixels.
#: ``image_size`` in both configs is documented as *the size of the largest
#: dimension of the generated image* — it is a bucket name, not a pixel count,
#: and the short edge follows from the aspect ratio.
#:
#: 1K and 2K are accepted by both request shapes. 4K appears only on
#: ``ImageConfig`` — the Gemini image-model path — so asking for it commits the
#: run to that path. See "Image generation providers" in ARCHITECTURE.md.
NAMED_OUTPUT_SIZES: Final[tuple[tuple[str, int], ...]] = (
    ("1K", 1024),
    ("2K", 2048),
    ("4K", 4096),
)


def supported_aspect_ratio(width: int, height: int) -> str:
    """The ratio label to send for a page of ``width`` x ``height`` pixels.

    Raises when the page's proportions are not within
    :data:`ASPECT_RATIO_TOLERANCE` of anything the API accepts, because there is
    no honest thing to send in that case: naming a ratio the page is not returns
    art that has to be cropped, and naming none returns a square.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"cannot request a {width}x{height} px image")
    wanted = width / height
    label, ratio = min(
        SUPPORTED_ASPECT_RATIOS, key=lambda pair: abs(pair[1] - wanted)
    )
    if abs(ratio - wanted) > ASPECT_RATIO_TOLERANCE * ratio:
        raise ValueError(
            f"{width}x{height} px is {wanted:.4f} wide-to-tall; the closest "
            f"aspect ratio the image API accepts is {label} ({ratio:.4f}), "
            "which is outside tolerance. Change the trim size or the margins "
            "rather than sending a ratio the page is not."
        )
    return label


def named_output_size(longest_edge: int) -> tuple[str, int]:
    """The smallest named size whose longest edge covers ``longest_edge``.

    Falls back to the largest size available when nothing covers it, so the
    request still asks for the most the API can give. It does **not** quietly
    lower the requirement: the shortfall shows up as effective DPI, and G9
    fails the page on it.
    """
    for label, pixels in NAMED_OUTPUT_SIZES:
        if pixels >= longest_edge:
            return label, pixels
    return NAMED_OUTPUT_SIZES[-1]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """What one run of the generator did."""

    manifest: BookManifest
    generated: tuple[str, ...]
    failed: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict:
        return {
            "generated": list(self.generated),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "ok": self.ok,
        }


def asset_id_for(page_id: str, attempt: int) -> str:
    """Stable version identifier: page plus zero-padded attempt."""
    return f"{page_id}.a{attempt:03d}"


def generate_book(
    manifest: BookManifest,
    provider: ImageProvider,
    assets_dir: str | Path,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: str | None = None,
    regenerate: tuple[str, ...] = (),
) -> GenerationOutcome:
    """Generate every art page that still needs work.

    ``regenerate`` names pages to redo even though they are approved or awaiting
    judgement. An unknown page id raises rather than being ignored: silently
    doing nothing when someone asked for a re-run is worse than refusing.
    """
    if manifest.prompt_plan is None:
        raise ProviderUnavailable(
            "the manifest carries no prompt plan; generation without a per-page "
            "plan cannot be reproduced or audited"
        )

    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now or _now()

    known = {p.page_id for p in manifest.spec.art_pages}
    unknown = sorted(set(regenerate) - known)
    if unknown:
        raise ProviderUnavailable(
            "cannot regenerate pages this book does not have: "
            + ", ".join(unknown)
        )
    forced = set(regenerate)

    records = list(manifest.assets)
    generated: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    for page in sorted(manifest.spec.art_pages, key=lambda p: p.index):
        if page.page_id not in forced and not _needs_work(manifest, page.page_id):
            skipped.append(page.page_id)
            continue

        prompt = manifest.prompt_plan.for_page(page.page_id)
        if prompt is None:
            failed.append(page.page_id)
            records.append(
                _failure_record(
                    page.page_id,
                    attempt=len(manifest.attempts(page.page_id)) + 1,
                    provider=getattr(provider, "name", "unknown"),
                    model=getattr(provider, "model", ""),
                    reason="no prompt planned for this page",
                    created_at=timestamp,
                )
            )
            continue

        record, ok = _attempt_page(
            manifest,
            records,
            page.page_id,
            prompt,
            provider,
            assets_dir,
            max_attempts,
            timestamp,
        )
        (generated if ok else failed).append(page.page_id)
        records.extend(record)

    return GenerationOutcome(
        manifest=manifest.with_assets(tuple(records)),
        generated=tuple(generated),
        failed=tuple(failed),
        skipped=tuple(skipped),
    )


def build_request(prompt: AssetPrompt, attempt: int = 1) -> GenerationRequest:
    """Turn one planned prompt into the request a provider receives.

    Shared with the single-page smoke test on purpose. A smoke test that
    assembled its own request would validate a message the batch never sends,
    and pass while the real run fails on the difference.
    """
    options = dict(prompt.generation_constraints)
    width = prompt.width or DEFAULT_WIDTH
    height = prompt.height or DEFAULT_HEIGHT

    # The pixels are the requirement — they come from the live area at 300 DPI —
    # so the ratio is derived from them rather than trusted from the plan. A
    # stated ratio still has to agree: a plan that says one shape and asks for
    # another is a planning bug, and finding it here costs nothing, whereas
    # finding it after the run costs forty pictures.
    ratio = supported_aspect_ratio(width, height)
    stated = str(options.get("aspect_ratio") or prompt.aspect_ratio or "").strip()
    if stated and stated != ratio:
        raise ValueError(
            f"{prompt.page_id}: the plan asks for aspect ratio {stated!r}, but "
            f"{width}x{height} px is {ratio}. Fix the prompt plan; do not send "
            "a ratio the page is not."
        )
    options["aspect_ratio"] = ratio

    # Output size is a bucket name, not a pixel count, and omitting it gets the
    # model's default — which is smaller than any print page needs. Ask for the
    # smallest bucket that still covers the longest edge. A plan may pin its own
    # value, and does not get overridden here.
    options.setdefault("image_size", named_output_size(max(width, height))[0])

    return GenerationRequest(
        page_id=prompt.page_id,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt=prompt.render(),
        prompt_positive=prompt.render_positive(),
        prompt_negative=prompt.render_negative(),
        width=width,
        height=height,
        options={k: v for k, v in options.items() if v is not None},
        attempt=attempt,
    )


def _needs_work(manifest: BookManifest, page_id: str) -> bool:
    """True when this page has nothing usable and nothing awaiting judgement.

    The pending case is the one that matters commercially: a page generated but
    not yet judged is work already paid for. Re-running the generator before QA
    has looked would buy it a second time and discard the first result.
    """
    attempts = manifest.attempts(page_id)
    if not attempts:
        return True
    if any(a.ships for a in attempts):
        return False
    return attempts[-1].status is not AssetStatus.PENDING


def _failure_record(
    page_id: str,
    *,
    attempt: int,
    provider: str,
    model: str,
    reason: str,
    created_at: str,
) -> AssetProvenance:
    return AssetProvenance(
        asset_id=asset_id_for(page_id, attempt),
        page_id=page_id,
        kind=AssetKind.IMAGE,
        ai_role=AIRole.GENERATED,
        content_hash="",
        tool=model or provider,
        provider=provider,
        attempt=attempt,
        status=AssetStatus.FAILED,
        failure_reason=reason,
        created_at=created_at,
    )


def _attempt_page(
    manifest: BookManifest,
    existing: list[AssetProvenance],
    page_id: str,
    prompt: AssetPrompt,
    provider: ImageProvider,
    assets_dir: Path,
    max_attempts: int,
    timestamp: str,
) -> tuple[list[AssetProvenance], bool]:
    """Try one page until it succeeds or the attempt budget runs out."""
    new: list[AssetProvenance] = []
    already = len([r for r in existing if r.page == page_id])

    for offset in range(max_attempts):
        attempt = already + offset + 1
        request = build_request(prompt, attempt)

        try:
            result = provider.generate(request)
        except ProviderUnavailable as exc:
            new.append(
                _failure_record(
                    page_id,
                    attempt=attempt,
                    provider=getattr(provider, "name", "unknown"),
                    model=getattr(provider, "model", ""),
                    reason=f"provider unavailable: {exc}",
                    created_at=timestamp,
                )
            )
            return new, False

        if not result.ok:
            new.append(
                _failure_record(
                    page_id,
                    attempt=attempt,
                    provider=result.provider,
                    model=result.model,
                    reason=result.reason or "unspecified provider failure",
                    created_at=timestamp,
                )
            )
            if not result.retryable:
                # Settled failures do not improve with repetition. The record is
                # kept — the page still has to stay visibly missing — but the
                # attempt budget is not spent proving the same point twice.
                return new, False
            continue

        filename = f"{asset_id_for(page_id, attempt)}.{result.file_format}"
        (assets_dir / filename).write_bytes(result.data)

        new.append(
            AssetProvenance(
                asset_id=asset_id_for(page_id, attempt),
                page_id=page_id,
                kind=AssetKind.IMAGE,
                ai_role=AIRole.GENERATED,
                content_hash=content_hash(result.data),
                tool=result.model or result.provider,
                provider=result.provider,
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                attempt=attempt,
                # PENDING, not APPROVED: the generator does not get to decide
                # that its own output is good enough. QA does.
                status=AssetStatus.PENDING,
                transformation=Transformation.GENERATED,
                width=result.width,
                height=result.height,
                file_format=result.file_format,
                path=f"assets/{filename}",
                created_at=timestamp,
            )
        )
        return new, True

    return new, False


def approve_asset(
    manifest: BookManifest, asset_id: str, *, supersede: bool = True
) -> BookManifest:
    """Mark one version approved, superseding any earlier approval for its page.

    Called by QA, not by the generator — the separation is the reason the QA
    auditor is read-only and this function lives outside it.
    """
    target = manifest.asset(asset_id)
    if target is None:
        raise ProviderUnavailable(f"no asset {asset_id!r} on this manifest")

    updated: list[AssetProvenance] = []
    from dataclasses import replace

    for record in manifest.assets:
        if record.asset_id == asset_id:
            updated.append(replace(record, status=AssetStatus.APPROVED))
        elif supersede and record.page == target.page and record.ships:
            updated.append(replace(record, status=AssetStatus.SUPERSEDED))
        else:
            updated.append(record)
    return manifest.with_assets(tuple(updated))


def reject_asset(manifest: BookManifest, asset_id: str, reason: str) -> BookManifest:
    """Mark one version rejected, keeping it in the history."""
    from dataclasses import replace

    updated = tuple(
        replace(record, status=AssetStatus.REJECTED, note=reason)
        if record.asset_id == asset_id
        else record
        for record in manifest.assets
    )
    return manifest.with_assets(updated)
