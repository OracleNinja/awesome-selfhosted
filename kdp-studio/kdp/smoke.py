"""One page, one provider call, before authorising forty.

A metered run over a whole book is the point of no return for the money. This
buys one image and puts it through the same request construction, the same
pixel inspection and the same resolution arithmetic the batch would use, so
that what it proves is actually about the batch.

**It does not touch the book.** The manifest is read and never written; the
image and its provenance land in a `smoke/` directory alongside. That keeps two
things true at once: nothing here can make the generation stage look finished,
and the artefact is a real `AssetProvenance` in the normal layout rather than a
special case. If the page is good, `kdp register-asset` promotes it into the
book — the command that already exists for exactly this.

Nothing here bypasses a gate. It runs the *inspection* a gate would run and
reports it; the gate itself still has to pass later, over the whole book.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import KDPError
from .generation import asset_id_for, build_request
from .inspection.image import ImageInspection, ImageLoadError, inspect_image
from .models.manifest import BookManifest
from .models.provenance import (
    AIRole,
    AssetKind,
    AssetProvenance,
    AssetStatus,
    Transformation,
    content_hash,
)
from .providers.base import GenerationResult, ImageProvider, ProviderUnavailable
from .specs import MIN_IMAGE_DPI, get_trim, interior_geometry


class SmokeTestError(KDPError):
    """The smoke test could not be attempted."""


@dataclass(frozen=True, slots=True)
class SmokeReport:
    """Everything a person needs to decide whether to authorise the batch."""

    book_id: str
    page_id: str
    provider: str
    model: str
    #: "ok" | "failed" — whether the provider produced an image at all.
    call_status: str
    call_reason: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_positive: str = ""
    prompt_negative: str = ""
    requested_width: int = 0
    requested_height: int = 0
    #: The generation options the request carried — aspect ratio and
    #: output size among them. Shown because the first paid run came back
    #: at the model's default size, and the request that asked for it was
    #: the one thing the report did not show.
    requested_options: dict = field(default_factory=dict)
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    bytes_size: int | None = None
    effective_dpi: float | None = None
    required_dpi: int = MIN_IMAGE_DPI
    live_width_in: float | None = None
    live_height_in: float | None = None
    inspection: dict | None = None
    findings: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    image_path: str | None = None
    provenance_path: str | None = None
    asset_id: str | None = None
    content_hash: str | None = None

    @property
    def resolution_ok(self) -> bool | None:
        if self.effective_dpi is None:
            return None
        return self.effective_dpi >= self.required_dpi

    @property
    def blocking_findings(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(f for f in self.findings if f[0] == "blocker")

    @property
    def would_pass_asset_qa(self) -> bool:
        """Whether this one page would clear the pixel checks G9 applies.

        Not a gate verdict and not a substitute for one — G9 judges the whole
        book, including pages this test never bought.
        """
        return (
            self.call_status == "ok"
            and self.resolution_ok is True
            and not self.blocking_findings
        )

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "page_id": self.page_id,
            "provider": self.provider,
            "model": self.model,
            "call_status": self.call_status,
            "call_reason": self.call_reason,
            "prompt": {
                "prompt_id": self.prompt_id,
                "version": self.prompt_version,
                "positive": self.prompt_positive,
                "negative": self.prompt_negative,
                "requested_size": [self.requested_width, self.requested_height],
                "requested_options": dict(self.requested_options),
            },
            "image": {
                "width": self.width,
                "height": self.height,
                "mime_type": self.mime_type,
                "bytes": self.bytes_size,
                "content_hash": self.content_hash,
                "path": self.image_path,
            },
            "resolution": {
                "effective_dpi": (
                    round(self.effective_dpi, 1) if self.effective_dpi else None
                ),
                "required_dpi": self.required_dpi,
                "live_area_in": [self.live_width_in, self.live_height_in],
                "ok": self.resolution_ok,
            },
            "inspection": self.inspection,
            "findings": [
                {"severity": s, "code": c, "message": m} for s, c, m in self.findings
            ],
            "provenance_path": self.provenance_path,
            "asset_id": self.asset_id,
            "would_pass_asset_qa": self.would_pass_asset_qa,
        }

    def render(self) -> str:
        """The human-readable form, which is what this command is really for."""
        lines = [
            f"Smoke test — {self.book_id} / {self.page_id}",
            "=" * 52,
            "",
            f"provider   {self.provider}",
            f"model      {self.model}",
            f"call       {self.call_status.upper()}"
            + (f" — {self.call_reason}" if self.call_reason else ""),
        ]
        if self.call_status != "ok":
            lines += ["", "No image was produced. Nothing was written."]
            return "\n".join(lines)

        dpi = f"{self.effective_dpi:.0f}" if self.effective_dpi else "?"
        verdict = "OK" if self.resolution_ok else "BELOW REQUIREMENT"
        lines += [
            "",
            f"image      {self.width} x {self.height} px, {self.mime_type}, "
            f"{self.bytes_size:,} bytes",
            f"requested  {self.requested_width} x {self.requested_height} px"
            + (
                "  ["
                + ", ".join(f"{k}={v}" for k, v in sorted(self.requested_options.items()))
                + "]"
                if self.requested_options
                else ""
            ),
            f"live area  {self.live_width_in} x {self.live_height_in} in",
            f"effective  {dpi} DPI against a required {self.required_dpi} — {verdict}",
            "",
            f"prompt     {self.prompt_id} {self.prompt_version}",
            f"  positive {self.prompt_positive.splitlines()[0] if self.prompt_positive else ''}",
            f"  negative {self.prompt_negative[:64]}",
            "",
            "inspection",
        ]
        if self.findings:
            for severity, code, message in self.findings:
                lines.append(f"  {severity:8} {code}: {message}")
        else:
            lines.append("  clean — no pixel findings")

        lines += [
            "",
            f"saved      {self.image_path}",
            f"provenance {self.provenance_path}",
            "",
        ]
        if self.would_pass_asset_qa:
            lines += [
                "This page would clear the pixel checks G9 applies. That is one",
                "page, not the book: G9 still judges all of them together, and",
                "the other gates are unchanged.",
                "",
                "To bring it into the book:",
                f"  python3 -m kdp register-asset <manifest> {self.page_id} "
                f"{self.image_path} \\",
                f"      --provider {self.provider} --model {self.model}",
            ]
        else:
            lines += [
                "This page would NOT clear the pixel checks G9 applies.",
                "Fix the prompt or the requested size before buying the rest.",
            ]
        return "\n".join(lines)


def run_smoke_test(
    manifest: BookManifest,
    page_id: str,
    provider: ImageProvider,
    out_dir: str | Path,
    *,
    now: str | None = None,
) -> SmokeReport:
    """Generate one page and measure it. Never writes to the manifest."""
    page = next((p for p in manifest.spec.art_pages if p.page_id == page_id), None)
    if page is None:
        known = ", ".join(p.page_id for p in manifest.spec.art_pages[:8])
        raise SmokeTestError(
            f"{page_id!r} is not an art page in this book. Art pages include: {known}"
        )

    if manifest.prompt_plan is None:
        raise SmokeTestError(
            "the book has no prompt plan, so there is nothing to send. Run the "
            "planning stage first."
        )
    prompt = manifest.prompt_plan.for_page(page_id)
    if prompt is None:
        raise SmokeTestError(f"no prompt is planned for {page_id!r}")

    # The same construction the batch uses. A smoke test that built its own
    # request would validate a message the real run never sends.
    request = build_request(prompt, attempt=1)

    provider_name = getattr(provider, "name", "unknown")
    model = getattr(provider, "model", "")

    try:
        result: GenerationResult = provider.generate(request)
    except ProviderUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        return SmokeReport(
            book_id=manifest.book_id, page_id=page_id, provider=provider_name,
            model=model, call_status="failed",
            call_reason=f"{type(exc).__name__}: {exc}",
            prompt_id=prompt.prompt_id, prompt_version=prompt.version,
            requested_width=request.width, requested_height=request.height,
            requested_options=dict(request.options),
        )

    common = dict(
        book_id=manifest.book_id, page_id=page_id,
        provider=result.provider or provider_name, model=result.model or model,
        prompt_id=prompt.prompt_id, prompt_version=prompt.version,
        prompt_positive=request.prompt_positive,
        prompt_negative=request.prompt_negative,
        requested_width=request.width, requested_height=request.height,
        requested_options=dict(request.options),
    )

    if not result.ok or not result.data:
        return SmokeReport(
            call_status="failed",
            call_reason=result.reason or "the provider returned no image",
            **common,
        )

    # --- write the artefact in the normal layout, outside the book ---------
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    asset_id = asset_id_for(page_id, 1)
    suffix = result.file_format or "png"
    image_path = out / "assets" / f"{asset_id}.{suffix}"
    image_path.write_bytes(result.data)

    # --- the same inspection a gate would run ------------------------------
    inspection: ImageInspection | None = None
    findings: list[tuple[str, str, str]] = []
    try:
        inspection = inspect_image(result.data)
        findings = [
            ("blocker" if issue.blocking else "warning", issue.code, issue.message)
            for issue in inspection.problems()
        ]
    except ImageLoadError as exc:
        findings = [("blocker", "smoke.unreadable", str(exc))]

    # --- the same resolution arithmetic G9 applies -------------------------
    effective_dpi = live_w = live_h = None
    try:
        geometry = interior_geometry(
            get_trim(manifest.spec.trim), manifest.spec.page_count, manifest.spec.bleed
        )
        live_w, live_h = geometry.live_width_in, geometry.live_height_in
        if inspection is not None:
            effective_dpi = inspection.effective_dpi(live_w, live_h)
    except Exception:  # noqa: BLE001 - geometry problems belong to G2/G5
        pass

    stamp = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    record = AssetProvenance(
        asset_id=asset_id,
        page_id=page_id,
        kind=AssetKind.IMAGE,
        ai_role=AIRole.GENERATED,
        content_hash=content_hash(result.data),
        tool=result.model or model or provider_name,
        provider=result.provider or provider_name,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        attempt=1,
        status=AssetStatus.PENDING,
        transformation=Transformation.GENERATED,
        width=result.width or (inspection.width if inspection else None),
        height=result.height or (inspection.height if inspection else None),
        file_format=suffix,
        path=str(image_path),
        created_at=stamp,
        note="single-page smoke test; not registered against the book",
    )
    provenance_path = out / f"{asset_id}.provenance.json"
    provenance_path.write_text(
        json.dumps(record.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    return SmokeReport(
        call_status="ok",
        width=record.width, height=record.height,
        mime_type=result.meta.get("mime_type") or f"image/{suffix}",
        bytes_size=len(result.data),
        effective_dpi=effective_dpi,
        live_width_in=live_w, live_height_in=live_h,
        inspection=inspection.to_dict() if inspection else None,
        findings=tuple(findings),
        image_path=str(image_path),
        provenance_path=str(provenance_path),
        asset_id=asset_id,
        content_hash=record.content_hash,
        **common,
    )
