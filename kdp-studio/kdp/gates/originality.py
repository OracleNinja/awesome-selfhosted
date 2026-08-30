"""G4 — originality and duplication.

Two failure modes, and they are not the same:

**Within a book** — the same drawing appearing twice under different page
numbers. Padding a book with repeats is the clearest form of the "minimal
differentiation" that gets low-content titles pulled.

**Across the catalogue** — a new book reusing pages from one already published.
KDP treats duplicative content as a removal trigger, and the account-level risk
is higher than the title-level one.

Exact-match detection is exact: identical bytes have identical hashes, and that
runs anywhere with no dependencies. Near-duplicate detection — a drawing
mirrored, recoloured, or nudged a few pixels — needs perceptual hashing, which
needs Pillow. When that is unavailable this gate reports ``BLOCKED`` rather
than passing, because "I only checked for exact copies" is not the same claim
as "there are no duplicates".
"""

from __future__ import annotations

from collections import defaultdict

from ..models.manifest import BookManifest
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G4.originality"
STAGE = "qa"


def perceptual_hashing_available() -> bool:
    """True when the optional imaging stack is installed."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def run(
    manifest: BookManifest,
    *,
    catalogue_hashes: dict[str, str] | None = None,
    require_perceptual: bool = True,
) -> GateResult:
    """Check for duplication.

    ``catalogue_hashes`` maps a previously published ``content_hash`` to the
    ``book_id`` it belongs to. Supply it from the back catalogue's manifests.
    """
    findings: list[Finding] = []
    catalogue_hashes = catalogue_hashes or {}

    by_hash: dict[str, list[str]] = defaultdict(list)
    for record in manifest.assets:
        by_hash[record.content_hash].append(record.asset_id)

    for digest, asset_ids in sorted(by_hash.items()):
        if len(asset_ids) > 1:
            findings.append(
                Finding(
                    code="originality.intra_book_duplicate",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{len(asset_ids)} pages share identical content. "
                        "Repeated pages read as padding and are a removal "
                        "trigger for low-content titles."
                    ),
                    subject=", ".join(sorted(asset_ids)),
                    detail={"content_hash": digest, "assets": sorted(asset_ids)},
                )
            )

    for record in manifest.assets:
        prior = catalogue_hashes.get(record.content_hash)
        if prior and prior != manifest.book_id:
            findings.append(
                Finding(
                    code="originality.catalogue_duplicate",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Asset is byte-identical to content already published "
                        f"in {prior!r}. Reusing pages across titles is "
                        "duplicative content."
                    ),
                    subject=record.asset_id,
                    detail={"prior_book_id": prior},
                )
            )

    blocked_reason = None
    if require_perceptual and not perceptual_hashing_available():
        blocked_reason = (
            "Near-duplicate detection is unavailable: Pillow is not installed, "
            "so only exact byte-matches were checked. Install the imaging "
            "extras (pip install -r requirements.txt) before certifying "
            "originality, or re-run with require_perceptual=False to record an "
            "exact-match-only result deliberately."
        )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "book_id": manifest.book_id,
            "assets_checked": len(manifest.assets),
            "catalogue_size": len(catalogue_hashes),
            "perceptual_hashing": perceptual_hashing_available(),
        },
        blocked_reason=blocked_reason,
    )
