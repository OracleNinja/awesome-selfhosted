"""G4 — originality and duplication.

Two failure modes, and they are not the same:

**Within a book** — the same drawing appearing twice under different page
numbers. Padding a book with repeats is the clearest form of the "minimal
differentiation" that gets low-content titles pulled.

**Across the catalogue** — a new book reusing pages from one already published.
KDP treats duplicative content as a removal trigger, and the account-level risk
is higher than the title-level one.

Exact-match detection is exact: identical bytes have identical hashes, and that
runs anywhere with no dependencies.

Near-duplicate detection — the same drawing rescaled, recoloured, or nudged —
uses a difference hash over the pixels. Two pages within a few bits of each
other are near-identical. Whether a page can be hashed depends on whether it
can be *read*, which is a per-file question: a PNG needs nothing installed, a
JPEG needs Pillow. A page that could not be read is reported as unchecked and
blocks the gate, because "I only checked for exact copies" is not the same
claim as "there are no duplicates".

A similarity score is a QA signal. It is not, and is never reported as, a
copyright determination.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..inspection.image import ImageLoadError, hamming, perceptual_hash
from ..models.manifest import BookManifest
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G4.originality"
STAGE = "qa"
VALIDATOR_VERSION = "2"

#: Bit distance below which two 64-bit difference hashes are treated as the
#: same drawing. 10 of 64 bits is the conventional working threshold: tight
#: enough that genuinely different subjects clear it, loose enough to catch a
#: rescale or a mirrored repeat.
NEAR_DUPLICATE_DISTANCE = 10


def perceptual_hashing_available() -> bool:
    """Whether the optional decoder is installed.

    Not what decides whether the gate can run — PNGs hash without it. Whether a
    given page could be hashed is answered by trying.
    """
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _resolve(path: str | None, assets_dir: str | Path | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if assets_dir is not None:
        rooted = Path(assets_dir) / candidate.name
        if rooted.is_file():
            return rooted
    return candidate if candidate.is_file() else None


def run(
    manifest: BookManifest,
    *,
    catalogue_hashes: dict[str, str] | None = None,
    require_perceptual: bool = True,
    assets_dir: str | Path | None = None,
    catalogue_phashes: dict[str, str] | None = None,
) -> GateResult:
    """Check for duplication.

    ``catalogue_hashes`` maps a previously published ``content_hash`` to the
    ``book_id`` it belongs to. ``catalogue_phashes`` does the same for
    perceptual hashes, catching a page republished after a re-render.
    """
    if not manifest.approved_assets:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence={"validator_version": VALIDATOR_VERSION, "book_id": manifest.book_id},
            blocked_reason=(
                "No approved assets, so there was nothing to compare. "
                "Originality cannot be certified over an empty book."
            ),
        )

    findings: list[Finding] = []
    catalogue_hashes = catalogue_hashes or {}
    catalogue_phashes = catalogue_phashes or {}

    # Only what ships is compared. A rejected attempt is not in the book, and
    # a *failed* one carries no content hash at all — grouping those together
    # made six dead attempts look like six duplicated pages.
    by_hash: dict[str, list[str]] = defaultdict(list)
    for record in manifest.approved_assets:
        if not record.content_hash:
            continue
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

    for record in manifest.approved_assets:
        prior = catalogue_hashes.get(record.content_hash) if record.content_hash else None
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

    # --- near-duplicate detection --------------------------------------
    unreadable: list[str] = []
    phashes: dict[str, int] = {}

    if require_perceptual:
        for record in manifest.approved_assets:
            source = _resolve(record.path, assets_dir)
            if source is None:
                unreadable.append(f"{record.page}: no readable file")
                continue
            try:
                phashes[record.page] = perceptual_hash(source)
            except ImageLoadError as exc:
                unreadable.append(f"{record.page}: {exc}")

        pages = sorted(phashes)
        for i, left in enumerate(pages):
            for right in pages[i + 1 :]:
                distance = hamming(phashes[left], phashes[right])
                if distance <= NEAR_DUPLICATE_DISTANCE:
                    findings.append(
                        Finding(
                            code="originality.near_duplicate",
                            severity=Severity.BLOCKER,
                            message=(
                                f"{left} and {right} are near-identical "
                                f"(perceptual distance {distance} of 64). Two "
                                "versions of one drawing read as padding."
                            ),
                            subject=f"{left}, {right}",
                            detail={"distance": distance},
                        )
                    )

        for page, value in sorted(phashes.items()):
            for prior_hash, prior_book in catalogue_phashes.items():
                if prior_book == manifest.book_id:
                    continue
                distance = hamming(value, int(prior_hash))
                if distance <= NEAR_DUPLICATE_DISTANCE:
                    findings.append(
                        Finding(
                            code="originality.catalogue_near_duplicate",
                            severity=Severity.BLOCKER,
                            message=(
                                f"{page} is near-identical to a page already "
                                f"published in {prior_book!r} (distance "
                                f"{distance} of 64). This is a QA signal for "
                                "review, not a copyright determination."
                            ),
                            subject=page,
                            detail={"distance": distance, "prior_book_id": prior_book},
                        )
                    )

    blocked_reason = None
    if not require_perceptual:
        blocked_reason = (
            "Near-duplicate detection was skipped by request, so only exact "
            "byte-matches were checked. A rescaled or mirrored repeat would not "
            "have been found."
        )
    elif unreadable:
        blocked_reason = (
            f"{len(unreadable)} page(s) could not be read, so near-duplicate "
            "detection did not cover them and only exact byte-matches were "
            "checked for those: " + "; ".join(unreadable[:5])
        )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "validator_version": VALIDATOR_VERSION,
            "book_id": manifest.book_id,
            "assets_checked": len(manifest.approved_assets),
            "attempts_recorded": len(manifest.assets),
            "catalogue_size": len(catalogue_hashes),
            "pages_hashed": len(phashes),
            "pages_unreadable": len(unreadable),
            "near_duplicate_distance": NEAR_DUPLICATE_DISTANCE,
            "optional_decoder": perceptual_hashing_available(),
        },
        blocked_reason=blocked_reason,
    )
