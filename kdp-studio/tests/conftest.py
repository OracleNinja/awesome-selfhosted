"""Shared fixtures.

The builders below produce a *valid* book by default, so each test can make
exactly one thing wrong and assert that exactly one thing is caught. Tests that
start from a broken fixture tend to pass for the wrong reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kdp.models import (  # noqa: E402
    AIRole,
    AssetKind,
    AssetProvenance,
    BookManifest,
    BookMetadata,
    BookSpec,
    MarketSignal,
    PageSpec,
    ResearchBrief,
    content_hash,
)


def make_pages(art_count: int = 4) -> tuple[PageSpec, ...]:
    """Front matter, then art on even pages backed by blanks on odd ones."""
    pages = [
        PageSpec("front-title", 1, "title"),
        PageSpec("front-copyright", 2, "copyright"),
    ]
    index = 3
    for n in range(art_count):
        # Art lands on even indices so every drawing backs onto a blank.
        pages.append(PageSpec(f"blank-{n}", index, "blank"))
        pages.append(PageSpec(f"art-{n}", index + 1, "art", subject=f"subject {n}"))
        index += 2
    return tuple(pages)


def make_spec(art_count: int = 4, **overrides) -> BookSpec:
    pages = overrides.pop("pages", make_pages(art_count))
    defaults = {
        "book_id": "book-001",
        "title": "Quiet Gardens",
        "trim": "8.5x11",
        "paper": "bw_white",
        "bleed": True,
        "page_count": len(pages),
        "pages": pages,
    }
    defaults.update(overrides)
    return BookSpec(**defaults)


def required_pixels(spec: BookSpec) -> tuple[int, int]:
    """Pixel size that clears KDP's 300 DPI floor over this book's live area.

    Derived, not fixed: the live area moves with trim, bleed and the gutter
    band the page count falls into, so a constant would be right for one book
    and quietly wrong for the next.
    """
    from kdp.specs import MIN_IMAGE_DPI, get_trim, interior_geometry

    geometry = interior_geometry(get_trim(spec.trim), spec.page_count, spec.bleed)
    dpi = MIN_IMAGE_DPI + 20
    return int(geometry.live_width_in * dpi), int(geometry.live_height_in * dpi)


def write_assets(
    spec: BookSpec, assets_dir: Path, ai_role: AIRole = AIRole.GENERATED
) -> tuple[AssetProvenance, ...]:
    """Write real print-resolution line art and return provenance for it.

    Gate tests inspect actual pixels, so a fixture that only *described* files
    would exercise the blocked path and nothing else. The pages are full size
    because the resolution check is one of the things under test — shrinking
    them to save time would quietly stop testing it.
    """
    from kdp.providers.mock import _png

    assets_dir.mkdir(parents=True, exist_ok=True)
    width, height = required_pixels(spec)
    records = []
    for page in spec.art_pages:
        data = _png(width, height, page.page_id.encode())
        name = f"{page.page_id}.png"
        (assets_dir / name).write_bytes(data)
        records.append(
            AssetProvenance(
                asset_id=page.page_id,
                page_id=page.page_id,
                kind=AssetKind.IMAGE,
                ai_role=ai_role,
                content_hash=content_hash(data),
                tool="line-art-generator" if ai_role is not AIRole.NONE else "hand-drawn",
                prompt_version="v1" if ai_role is AIRole.GENERATED else None,
                width=width,
                height=height,
                file_format="png",
                path=f"assets/{name}",
            )
        )
    return tuple(records)


def make_assets(spec: BookSpec, ai_role: AIRole = AIRole.GENERATED) -> tuple:
    """Provenance with no files behind it, for record-level tests."""
    return tuple(
        AssetProvenance(
            asset_id=page.page_id,
            page_id=page.page_id,
            kind=AssetKind.IMAGE,
            ai_role=ai_role,
            content_hash=content_hash(f"art-bytes-{page.page_id}".encode()),
            tool="line-art-generator" if ai_role is not AIRole.NONE else "hand-drawn",
            prompt_version="v1" if ai_role is AIRole.GENERATED else None,
        )
        for page in spec.art_pages
    )


def make_metadata(**overrides) -> BookMetadata:
    defaults = {
        "book_id": "book-001",
        "title": "Quiet Gardens",
        "author": "A. Author",
        "description": "A calm coloring book of original garden scenes. " * 4,
        "keywords": ("coloring book", "garden", "adult coloring", "relaxing"),
        "categories": ("Crafts, Hobbies & Home > Crafts & Hobbies",),
        "ai_disclosure_confirmed": True,
    }
    defaults.update(overrides)
    return BookMetadata(**defaults)


@pytest.fixture
def spec() -> BookSpec:
    """A legal book: 26 pages (2 front matter + 12 art, each backed by a blank).

    Above KDP's 24-page minimum on purpose. A default fixture that is already
    illegal makes downstream gates bail on geometry before they reach the check
    a test is actually about, which hides regressions.
    """
    return make_spec(art_count=12)


@pytest.fixture
def manifest(spec: BookSpec) -> BookManifest:
    """A book described but not built: provenance with no files behind it."""
    return BookManifest(
        spec=spec,
        assets=make_assets(spec),
        metadata=make_metadata(),
        stage="qa",
    )


def small_book_pages() -> tuple[PageSpec, ...]:
    """26 printable pages carrying only four drawings.

    Four rather than twelve because these pages are built at print resolution —
    7 MB each before compression — and every file-level test pays for them.
    Four is enough to exercise ordering, duplication and placement.
    """
    pages = [PageSpec("PAGE-001", 1, "title"), PageSpec("PAGE-002", 2, "copyright")]
    index = 3
    for n in range(4):
        pages.append(PageSpec(f"PAGE-{index:03d}", index, "blank"))
        pages.append(
            PageSpec(f"PAGE-{index + 1:03d}", index + 1, "art", subject=f"study {n}")
        )
        index += 2
    while index <= 26:
        pages.append(PageSpec(f"PAGE-{index:03d}", index, "blank"))
        index += 1
    return tuple(pages)


@pytest.fixture
def built_manifest(tmp_path: Path) -> tuple[BookManifest, Path]:
    """A printable book with real print-resolution line art on disk."""
    pages = small_book_pages()
    spec = make_spec(pages=pages, page_count=len(pages))
    assets_dir = tmp_path / "assets"
    return (
        BookManifest(
            spec=spec,
            assets=write_assets(spec, assets_dir),
            metadata=make_metadata(),
            stage="qa",
        ),
        assets_dir,
    )


@pytest.fixture
def brief() -> ResearchBrief:
    return ResearchBrief(
        brief_id="brief-001",
        segment="adult-botanical",
        signals=(
            MarketSignal(
                signal_id="s1",
                segment="adult-botanical",
                price_usd=8.0,
                page_count=60,
                trim="8.5x11",
                demand_band="high",
                observations=("reviewers repeatedly ask for single-sided pages",),
            ),
        ),
        opportunity="Single-sided botanical line art at a gentler complexity curve.",
        recommended_trim="8.5x11",
        recommended_page_count=60,
    )
