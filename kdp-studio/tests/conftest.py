"""Shared fixtures.

The builders below produce a *valid* book by default, so each test can make
exactly one thing wrong and assert that exactly one thing is caught. Tests that
start from a broken fixture tend to pass for the wrong reason.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
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


# --- a stub shaped like the google-genai response types --------------------
#
# Mirrors the attribute names the installed SDK actually carries, so a test
# that passes here is testing the real shape:
#   GenerateImagesResponse.generated_images[].image.image_bytes / .mime_type
#                                            .rai_filtered_reason
# Shared by the provider tests and the smoke-test tests. Nothing touches a
# network, so neither needs a key and neither ever pays for a picture.


@dataclass
class StubImage:
    image_bytes: bytes | None
    mime_type: str = "image/png"


@dataclass
class StubGenerated:
    image: StubImage | None = None
    rai_filtered_reason: str | None = None
    enhanced_prompt: str | None = None


@dataclass
class StubResponse:
    generated_images: list | None


class StubModels:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls: list[dict] = []

    def generate_images(self, *, model, prompt, config):
        self.calls.append({"model": model, "prompt": prompt, "config": config})
        if self.error:
            raise self.error
        return self.response


class StubClient:
    """Stands in for google.genai.Client."""

    def __init__(self, response=None, error=None):
        self.models = StubModels(response, error)


def stub_client_returning(data: bytes) -> StubClient:
    return StubClient(StubResponse([StubGenerated(image=StubImage(data))]))


# --- google.genai generate_content, for the Gemini image provider -----------
#
# Shaped to the installed SDK's own types, attribute for attribute:
# GenerateContentResponse.candidates[].content.parts[].inline_data is a Blob
# with .data and .mime_type; Candidate carries .finish_reason and
# .finish_message; the response carries .prompt_feedback.block_reason,
# .model_version, .response_id and .usage_metadata. A test asserting on names
# the SDK does not use would pass while production failed, so
# tests/test_google_gemini.py checks these against the real types.


@dataclass
class StubBlob:
    """google.genai.types.Blob."""

    data: bytes | None = None
    mime_type: str | None = "image/png"


@dataclass
class StubPart:
    """google.genai.types.Part. Exactly one field is meant to be set."""

    inline_data: StubBlob | None = None
    text: str | None = None


@dataclass
class StubContent:
    """google.genai.types.Content."""

    parts: list | None = None
    role: str = "model"


@dataclass
class StubCandidate:
    """google.genai.types.Candidate."""

    content: StubContent | None = None
    finish_reason: str | None = "STOP"
    finish_message: str | None = None


@dataclass
class StubPromptFeedback:
    """google.genai.types.GenerateContentResponsePromptFeedback."""

    block_reason: str | None = None
    block_reason_message: str | None = None


@dataclass
class StubUsage:
    """google.genai.types.GenerateContentResponseUsageMetadata."""

    prompt_token_count: int | None = None
    candidates_token_count: int | None = None
    total_token_count: int | None = None


@dataclass
class StubContentResponse:
    """google.genai.types.GenerateContentResponse."""

    candidates: list | None = None
    prompt_feedback: StubPromptFeedback | None = None
    model_version: str | None = None
    response_id: str | None = None
    usage_metadata: StubUsage | None = None


class StubContentModels:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        return self.response


class StubContentClient:
    """Stands in for google.genai.Client on the generate_content path."""

    def __init__(self, response=None, error=None):
        self.models = StubContentModels(response, error)


def image_response(
    data: bytes,
    mime_type: str = "image/png",
    *,
    finish_reason: str = "STOP",
    text: str | None = None,
) -> StubContentResponse:
    """A response carrying one inline image, as the real API returns one."""
    parts = [StubPart(inline_data=StubBlob(data=data, mime_type=mime_type))]
    if text is not None:
        # The model is entitled to return prose alongside the picture, and the
        # provider has to walk past it rather than treat it as the image.
        parts.insert(0, StubPart(text=text))
    return StubContentResponse(
        candidates=[StubCandidate(content=StubContent(parts=parts),
                                  finish_reason=finish_reason)],
        model_version="stub-model-version",
        response_id="stub-response-id",
        usage_metadata=StubUsage(
            prompt_token_count=11, candidates_token_count=22, total_token_count=33
        ),
    )


def gemini_client_returning(data: bytes, **kwargs) -> StubContentClient:
    return StubContentClient(image_response(data, **kwargs))
