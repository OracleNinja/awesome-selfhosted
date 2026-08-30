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

Retries are bounded per page rather than globally, so one hopeless page cannot
exhaust the budget for the rest of the book.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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

#: Requested pixel size. 300 DPI over an 8.5x11 live area, rounded.
DEFAULT_WIDTH = 2400
DEFAULT_HEIGHT = 3000


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
) -> GenerationOutcome:
    """Generate every art page that does not already have an approved asset."""
    if manifest.prompt_plan is None:
        raise ProviderUnavailable(
            "the manifest carries no prompt plan; generation without a per-page "
            "plan cannot be reproduced or audited"
        )

    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now or _now()

    records = list(manifest.assets)
    generated: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    for page in sorted(manifest.spec.art_pages, key=lambda p: p.index):
        if not _needs_work(manifest, page.page_id):
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
        request = GenerationRequest(
            page_id=page_id,
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            prompt=prompt.render(),
            width=prompt.width or DEFAULT_WIDTH,
            height=prompt.height or DEFAULT_HEIGHT,
            options=prompt.generation_constraints,
            attempt=attempt,
        )

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
