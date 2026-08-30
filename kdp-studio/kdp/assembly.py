"""Building the interior and the cover.

Both builders are pure functions of the manifest plus the asset bytes: same
inputs, same PDF, byte for byte. Nothing here consults the clock, so a rebuild
that produces a different hash means the *book* changed — which is exactly the
signal the approval fingerprint depends on.

The gutter alternates. On a recto the inside margin is on the left; on a verso
it is on the right, and the bleed is on the opposite edge. Getting this
backwards puts every drawing a quarter-inch into the spine, and the resulting
PDF looks perfectly fine until it is printed and bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import KDPError
from .imaging import RawImage, decode_png
from .models.manifest import BookManifest
from .pdfwrite import PDFBuilder, Page
from .specs import (
    BLEED_IN,
    cover_geometry,
    get_paper,
    get_trim,
    interior_geometry,
)


class AssemblyError(KDPError):
    """The book could not be assembled from what the manifest describes."""


@dataclass(frozen=True, slots=True)
class LiveBox:
    """The printable rectangle on one page, in inches from the bottom-left."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def live_box(
    trim_width: float,
    trim_height: float,
    outer: float,
    gutter: float,
    bleed: float,
    recto: bool,
) -> LiveBox:
    """Where art may go on one page.

    ``recto`` is a right-hand page: gutter on the left, bleed on the right.
    """
    if recto:
        x0 = gutter
        x1 = trim_width - outer
    else:
        # A verso's bleed is on the left, so the trim area starts inset by it.
        x0 = bleed + outer
        x1 = bleed + trim_width - gutter
    return LiveBox(x0=x0, y0=bleed + outer, x1=x1, y1=bleed + trim_height - outer)


def fit(image: RawImage, box: LiveBox) -> tuple[float, float, float, float]:
    """Largest centred placement of ``image`` inside ``box``, aspect preserved.

    Returns ``(x, y, width, height)`` in inches.
    """
    if image.width <= 0 or image.height <= 0:
        raise AssemblyError("image has a non-positive dimension")
    scale = min(box.width / image.width, box.height / image.height)
    width = image.width * scale
    height = image.height * scale
    return (
        box.x0 + (box.width - width) / 2,
        box.y0 + (box.height - height) / 2,
        width,
        height,
    )


def build_interior(manifest: BookManifest, assets_dir: str | Path) -> bytes:
    """Assemble the interior PDF from the approved assets."""
    spec = manifest.spec
    trim = get_trim(spec.trim)
    geometry = interior_geometry(trim, spec.page_count, spec.bleed)
    bleed = BLEED_IN if spec.bleed else 0.0
    assets_dir = Path(assets_dir)

    if not spec.pages:
        raise AssemblyError(f"book {spec.book_id!r} describes no pages")

    builder = PDFBuilder()

    for page_spec in sorted(spec.pages, key=lambda p: p.index):
        page = Page(
            width_in=geometry.page_width_in, height_in=geometry.page_height_in
        )
        recto = page_spec.index % 2 == 1
        box = live_box(
            trim.width_in,
            trim.height_in,
            geometry.outer_margin_in,
            geometry.gutter_in,
            bleed,
            recto,
        )

        if page_spec.role == "art":
            approved = manifest.approved_for(page_spec.page_id)
            if approved is None:
                raise AssemblyError(
                    f"art page {page_spec.page_id!r} has no approved asset; "
                    "assembly never substitutes a placeholder"
                )
            if not approved.path:
                raise AssemblyError(
                    f"approved asset for {page_spec.page_id!r} records no path"
                )
            source = assets_dir / Path(approved.path).name
            if not source.exists():
                raise AssemblyError(
                    f"approved asset for {page_spec.page_id!r} is recorded at "
                    f"{approved.path} but no file is there"
                )
            image = decode_png(source.read_bytes())
            x, y, width, height = fit(image, box)
            page.image = image
            page.image_x_in, page.image_y_in = x, y
            page.image_width_in, page.image_height_in = width, height

        elif page_spec.role == "title":
            page.add_text(box.x0, box.y1 - 1.0, 28, spec.title)
            if spec.subtitle:
                page.add_text(box.x0, box.y1 - 1.5, 14, spec.subtitle)

        elif page_spec.role == "copyright":
            author = manifest.metadata.author if manifest.metadata else ""
            lines = [f"Copyright {author}".strip(), "All rights reserved."]
            disclosure = manifest.disclosure()
            if disclosure.complete and disclosure.required:
                # Stated in the book as well as on the KDP form: the form
                # answer is internal to Amazon, and a reader deserves to know.
                lines.append(
                    "Contains AI-generated content ("
                    + ", ".join(disclosure.categories)
                    + ")."
                )
            for offset, line in enumerate(lines):
                page.add_text(box.x0, box.y0 + 1.0 - (offset * 0.22), 9, line)

        builder.add_page(page)

    return builder.build()


def build_cover(manifest: BookManifest, cover_art: str | Path | None = None) -> bytes:
    """Assemble the full-wrap cover PDF."""
    spec = manifest.spec
    trim = get_trim(spec.trim)
    paper = get_paper(spec.paper)
    cover = cover_geometry(trim, paper, spec.page_count)

    page = Page(width_in=cover.total_width_in, height_in=cover.total_height_in)

    if cover_art is not None:
        source = Path(cover_art)
        if not source.exists():
            raise AssemblyError(f"cover art is recorded at {cover_art} but absent")
        image = decode_png(source.read_bytes())
        front = LiveBox(
            x0=cover.front_panel_origin_x_in,
            y0=BLEED_IN,
            x1=cover.front_panel_origin_x_in + trim.width_in,
            y1=BLEED_IN + trim.height_in,
        )
        x, y, width, height = fit(image, front)
        page.image = image
        page.image_x_in, page.image_y_in = x, y
        page.image_width_in, page.image_height_in = width, height

    # Front panel copy, inset from the fold and the trim edge by the safe margin.
    front_x = cover.front_panel_origin_x_in + 0.5
    page.add_text(front_x, BLEED_IN + trim.height_in - 1.5, 30, spec.title)
    if spec.subtitle:
        page.add_text(front_x, BLEED_IN + trim.height_in - 2.1, 16, spec.subtitle)
    if manifest.metadata and manifest.metadata.author:
        page.add_text(front_x, BLEED_IN + 0.8, 16, manifest.metadata.author)

    # Back panel copy. The lower-right of the back cover is reserved for the
    # barcode Amazon places itself, so nothing is written there.
    page.add_text(BLEED_IN + 0.5, BLEED_IN + trim.height_in - 1.5, 12, spec.title)

    if cover.spine_text_allowed and cover.spine_text_width_in > 0:
        page.add_text(
            BLEED_IN + trim.width_in + (cover.spine_width_in / 2),
            BLEED_IN + (trim.height_in / 2),
            9,
            spec.title[:40],
        )

    builder = PDFBuilder()
    builder.add_page(page)
    return builder.build()
