"""The publication package.

A ZIP holding everything a human needs to complete a KDP submission by hand:
the two PDFs, the listing, the disclosure answer, the reports, the manifest and
the approval record.

It refuses to build without a current approval. That refusal is the last line
of the human-approval requirement — not because the ZIP is dangerous, but
because a package that exists implies a book that was signed off, and a package
built from an unapproved book would quietly become one someone submits.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .errors import KDPError
from .gates.base import GateResult
from .models.artifacts import MATERIAL_ARTIFACTS
from .models.manifest import BookManifest
from .review import build_review


class PackagingError(KDPError):
    """The publication package could not be built."""


@dataclass(frozen=True, slots=True)
class PackageResult:
    path: Path
    content_hash: str
    entries: tuple[str, ...]


def build_package(
    manifest: BookManifest,
    destination: str | Path,
    results: list[GateResult] | None = None,
    *,
    require_approval: bool = True,
) -> PackageResult:
    """Write the publication package, or refuse and say why."""
    if require_approval and not manifest.approval_is_current():
        status = manifest.approval.status.value
        detail = (
            "the book has changed since it was approved"
            if status == "approved"
            else f"approval status is {status!r}"
        )
        raise PackagingError(
            f"cannot build a publication package for {manifest.book_id!r}: "
            f"{detail}. A package implies a book someone signed off on. Run the "
            "review package, have a person read it, and record their decision."
        )

    results = results or []
    missing = [
        kind.value for kind in MATERIAL_ARTIFACTS if manifest.artifact(kind) is None
    ]
    if missing:
        raise PackagingError(
            f"cannot package {manifest.book_id!r}: missing {', '.join(missing)}"
        )

    disclosure = manifest.disclosure()
    entries: list[str] = []
    buffer = BytesIO()

    # Fixed order and fixed timestamps so the same book packages to the same
    # bytes; a changing hash then means the book changed, not the clock.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        def write(name: str, data: bytes | str) -> None:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(
                info, data if isinstance(data, bytes) else data.encode("utf-8")
            )
            entries.append(name)

        for kind in MATERIAL_ARTIFACTS:
            artifact = manifest.artifact(kind)
            source = Path(artifact.path)
            if not source.exists():
                raise PackagingError(
                    f"{kind.value} is recorded at {artifact.path} but absent"
                )
            write(f"{kind.value}.pdf", source.read_bytes())

        write("manifest.json", manifest.to_json())
        write("review.md", build_review(manifest, results))
        write(
            "gate_results.json",
            json.dumps([r.to_dict() for r in results], sort_keys=True, indent=2) + "\n",
        )
        write(
            "ai_disclosure.json",
            json.dumps(
                {
                    "statement": disclosure.statement(),
                    **disclosure.to_dict(),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )
        write("approval.json", json.dumps(manifest.approval.to_dict(), sort_keys=True, indent=2) + "\n")

        if manifest.metadata:
            write(
                "listing.json",
                json.dumps(
                    {
                        **manifest.metadata.to_dict(),
                        "trim": manifest.spec.trim,
                        "paper": manifest.spec.paper,
                        "page_count": manifest.spec.page_count,
                        "bleed": manifest.spec.bleed,
                        "price": (
                            manifest.economics.selected_scenario.to_dict()
                            if manifest.economics
                            and manifest.economics.selected_scenario
                            else None
                        ),
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            )

        write("SUBMISSION_CHECKLIST.md", _checklist(manifest))

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = buffer.getvalue()
    target.write_bytes(payload)

    import hashlib

    return PackageResult(
        path=target,
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        entries=tuple(entries),
    )


def _checklist(manifest: BookManifest) -> str:
    """The steps a human takes in the KDP dashboard. Nothing does this for them."""
    spec = manifest.spec
    disclosure = manifest.disclosure()
    metadata = manifest.metadata
    price = manifest.economics.selected_scenario if manifest.economics else None

    lines = [
        f"# Submission checklist — {spec.title}",
        "",
        "This package was prepared automatically. **Submission is manual.** "
        "Nothing in this pipeline uploads to Amazon.",
        "",
        "## 1. Create the paperback in KDP",
        "",
        f"- Title: {metadata.title if metadata else spec.title}",
        f"- Subtitle: {(metadata.subtitle if metadata else spec.subtitle) or '—'}",
        f"- Author: {metadata.author if metadata else '—'}",
        f"- Language: {metadata.language if metadata else 'english'}",
        "",
        "## 2. Answer the AI-content question",
        "",
        f"**{disclosure.statement()}**",
        "",
    ]
    if disclosure.required:
        lines += [
            "Tick the AI-generated content box and select: "
            + ", ".join(disclosure.categories) + ".",
            "",
            "This answer is derived from per-asset provenance, not typed by "
            "hand. See `ai_disclosure.json` for the assets behind it.",
        ]
    else:
        lines.append("Do not tick the AI-generated content box.")

    lines += [
        "",
        "## 3. Keywords and categories",
        "",
    ]
    if metadata:
        for keyword in metadata.keywords:
            lines.append(f"- {keyword}")
        lines.append("")
        for category in metadata.categories:
            lines.append(f"- {category}")

    lines += [
        "",
        "## 4. Print options",
        "",
        f"- Trim: {spec.trim}",
        f"- Paper: {spec.paper}",
        f"- Bleed: {'yes' if spec.bleed else 'no'}",
        f"- Page count: {spec.page_count}",
        "",
        "## 5. Upload",
        "",
        "- Interior: `interior_pdf.pdf`",
        "- Cover: `cover_pdf.pdf`",
        "",
        "Use KDP's previewer and confirm it reports no errors. The previewer is "
        "the authority; this package's preflight is a pre-check, not a "
        "substitute.",
        "",
        "## 6. Price",
        "",
    ]
    if price:
        lines += [
            f"- List price: ${price.list_price_usd:.2f} ({price.marketplace})",
            f"- Projected print cost: ${price.print_cost_usd:.2f}",
            f"- Projected net royalty: ${price.net_royalty_usd:.2f} per copy",
            "",
            "Projected figures are estimates. KDP shows the real printing cost "
            "and royalty at this step — check them against these before "
            "publishing.",
        ]
    else:
        lines.append("- No price selected.")

    lines += [
        "",
        "## 7. Before you press Publish",
        "",
        "- [ ] KDP previewer reports no errors",
        "- [ ] The AI-content answer above is recorded",
        "- [ ] Printing cost and royalty match expectations",
        "- [ ] You have not exceeded three new titles in the last 24 hours",
        "",
        f"Approved by: {manifest.approval.reviewer or '—'} on "
        f"{manifest.approval.decided_at or '—'}",
        f"Book fingerprint: `{manifest.production_fingerprint()}`",
        "",
    ]
    return "\n".join(lines)
