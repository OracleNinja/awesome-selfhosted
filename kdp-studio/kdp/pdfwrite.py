"""A small, deterministic PDF writer.

Writes exactly what an illustrated book needs: fixed-size pages carrying one
placed image, plus simple text. No fonts are embedded — only the base-14
Helvetica, which every PDF reader has — so the output stays small and the
writer stays short enough to audit.

**Determinism is the point.** No timestamps, no object ids that depend on
iteration order, no random identifiers. Building the same book twice produces
byte-identical PDFs, which is what makes the artefact hashes in the manifest
meaningful and lets the approval fingerprint mean something.

It is deliberately *not* the thing that verifies its own output. Interior and
cover QA read the result with ``pypdf`` — an independent implementation — so a
bug here cannot certify itself as correct.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

from .imaging import RawImage

#: PDF's unit is 1/72 inch.
POINTS_PER_INCH = 72.0


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


@dataclass(slots=True)
class Page:
    """One page: a size in inches, an optional image, and optional text."""

    width_in: float
    height_in: float
    image: RawImage | None = None
    #: Where the image goes, in inches from the bottom-left corner.
    image_x_in: float = 0.0
    image_y_in: float = 0.0
    image_width_in: float = 0.0
    image_height_in: float = 0.0
    #: (x_in, y_in, size_pt, text)
    texts: list[tuple[float, float, float, str]] = field(default_factory=list)

    def add_text(self, x_in: float, y_in: float, size_pt: float, text: str) -> None:
        self.texts.append((x_in, y_in, size_pt, text))


class PDFBuilder:
    """Assembles pages into a PDF byte string."""

    def __init__(self) -> None:
        self.pages: list[Page] = []

    def add_page(self, page: Page) -> Page:
        self.pages.append(page)
        return page

    def build(self) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            """Append an object and return its 1-based number."""
            objects.append(body)
            return len(objects)

        # Object 1 is the catalog and object 2 the page tree; both are written
        # last but reserved first so page objects can point at the tree.
        objects.append(b"")  # 1: catalog
        objects.append(b"")  # 2: pages

        page_refs: list[int] = []

        for page in self.pages:
            resources = b"<< >>"
            content = []

            if page.image is not None:
                img = page.image
                # Fixed level, not maximal: determinism needs it constant,
                # and level 9 on a full-page image is most of a build's time.
                stream = zlib.compress(img.pixels, 6)
                image_obj = add(
                    b"<</Type/XObject/Subtype/Image"
                    + f"/Width {img.width}/Height {img.height}".encode()
                    + f"/ColorSpace/{img.colour_space}".encode()
                    + b"/BitsPerComponent 8/Filter/FlateDecode"
                    + f"/Length {len(stream)}>>\nstream\n".encode()
                    + stream
                    + b"\nendstream"
                )
                resources = (
                    b"<</XObject<</Im0 " + str(image_obj).encode() + b" 0 R>>"
                    b"/Font<</F1 " + b"FONTREF" + b" 0 R>>>>"
                )
                # PDF places an image by scaling the unit square, so the CTM
                # carries the placed size in points.
                content.append(
                    "q {w:.4f} 0 0 {h:.4f} {x:.4f} {y:.4f} cm /Im0 Do Q".format(
                        w=page.image_width_in * POINTS_PER_INCH,
                        h=page.image_height_in * POINTS_PER_INCH,
                        x=page.image_x_in * POINTS_PER_INCH,
                        y=page.image_y_in * POINTS_PER_INCH,
                    )
                )
            else:
                resources = b"<</Font<</F1 FONTREF 0 R>>>>"

            for x_in, y_in, size, text in page.texts:
                content.append(
                    "BT /F1 {size:.2f} Tf {x:.4f} {y:.4f} Td ({text}) Tj ET".format(
                        size=size,
                        x=x_in * POINTS_PER_INCH,
                        y=y_in * POINTS_PER_INCH,
                        text=_escape(text),
                    )
                )

            body = "\n".join(content).encode("latin-1", "replace")
            stream = zlib.compress(body, 9)
            content_obj = add(
                b"<</Filter/FlateDecode/Length "
                + str(len(stream)).encode()
                + b">>\nstream\n"
                + stream
                + b"\nendstream"
            )

            page_obj = add(
                b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 "
                + "{:.4f} {:.4f}".format(
                    page.width_in * POINTS_PER_INCH, page.height_in * POINTS_PER_INCH
                ).encode()
                + b"]/Resources "
                + resources
                + b"/Contents "
                + str(content_obj).encode()
                + b" 0 R>>"
            )
            page_refs.append(page_obj)

        font_obj = add(
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"
        )
        objects = [o.replace(b"FONTREF", str(font_obj).encode()) for o in objects]

        objects[0] = b"<</Type/Catalog/Pages 2 0 R>>"
        kids = b"".join(f"{ref} 0 R ".encode() for ref in page_refs).strip()
        objects[1] = (
            b"<</Type/Pages/Kids["
            + kids
            + b"]/Count "
            + str(len(page_refs)).encode()
            + b">>"
        )

        out = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (
            b"trailer\n<</Size "
            + str(len(objects) + 1).encode()
            + b"/Root 1 0 R>>\nstartxref\n"
            + str(xref_at).encode()
            + b"\n%%EOF\n"
        )
        return bytes(out)
