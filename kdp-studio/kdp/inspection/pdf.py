"""Structural inspection of a built PDF.

Reads the file with ``pypdf`` — deliberately a different implementation from
``kdp/pdfwrite.py``, which produces it. A writer that verified its own output
would certify its own bugs, so the two are kept apart and the dependency is
accepted for exactly that reason.

Without ``pypdf`` there is no second implementation and nothing here can run.
That is a real absence of capability, so the gates block rather than pass. The
alternative — hand-rolling a reader for the format this package also writes —
would give an answer without giving an independent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import KDPError

#: PDF's unit.
POINTS_PER_INCH = 72.0

#: Page dimensions are compared to this tolerance, in inches. Generous enough
#: to absorb the rounding in a PDF's decimal MediaBox, tight enough that a
#: missing bleed (0.125") is never inside it.
DIMENSION_TOLERANCE_IN = 0.01


class PDFLoadError(KDPError):
    """A PDF could not be opened or is structurally unusable."""


def pdf_reader_available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class PageInspection:
    """One page as the reader sees it."""

    index: int
    width_in: float
    height_in: float
    image_count: int
    #: (width, height) in pixels for each embedded image.
    image_sizes: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    has_text: bool = False

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "width_in": round(self.width_in, 4),
            "height_in": round(self.height_in, 4),
            "image_count": self.image_count,
            "image_sizes": [list(s) for s in self.image_sizes],
            "has_text": self.has_text,
        }


@dataclass(frozen=True, slots=True)
class PDFInspection:
    """What the PDF actually contains."""

    page_count: int
    pages: tuple[PageInspection, ...]
    encrypted: bool = False
    not_checked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def distinct_page_sizes(self) -> tuple[tuple[float, float], ...]:
        return tuple(
            sorted({(round(p.width_in, 3), round(p.height_in, 3)) for p in self.pages})
        )

    def pages_with_images(self) -> tuple[int, ...]:
        return tuple(p.index for p in self.pages if p.image_count)

    def to_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "encrypted": self.encrypted,
            "distinct_page_sizes": [list(s) for s in self.distinct_page_sizes],
            "pages_with_images": list(self.pages_with_images()),
            "not_checked": list(self.not_checked),
            # Per-page detail is bounded: a 500-page interior would otherwise
            # bury the gate result it is evidence for.
            "pages": [p.to_dict() for p in self.pages[:8]],
        }


def inspect_pdf(path: str | Path, *, extract_images: bool = True) -> PDFInspection:
    """Open a PDF and measure it. Raises :class:`PDFLoadError` if unreadable."""
    if not pdf_reader_available():
        raise PDFLoadError(
            "pypdf is not installed, so no independent reader is available to "
            "inspect this PDF. Install the production extras "
            "(pip install -r requirements.txt)."
        )

    import pypdf
    from pypdf.errors import PdfReadError

    target = Path(path)
    if not target.exists():
        raise PDFLoadError(f"no file at {path}")
    if not target.is_file():
        raise PDFLoadError(f"{path} is not a file")

    try:
        reader = pypdf.PdfReader(str(target))
        encrypted = reader.is_encrypted
        raw_pages = list(reader.pages)
    except PdfReadError as exc:
        raise PDFLoadError(f"PDF will not open: {exc}") from None
    except OSError as exc:
        raise PDFLoadError(f"PDF cannot be read: {exc}") from None
    except Exception as exc:  # noqa: BLE001
        # pypdf raises a wide and undocumented range on malformed input —
        # KeyError, RecursionError, struct errors. A gate must stop the book,
        # not the run, so everything is funnelled into one typed failure.
        raise PDFLoadError(
            f"PDF is malformed ({type(exc).__name__}: {exc})"
        ) from None

    not_checked: list[str] = []
    pages: list[PageInspection] = []

    for index, page in enumerate(raw_pages, start=1):
        try:
            box = page.mediabox
            width_in = float(box.width) / POINTS_PER_INCH
            height_in = float(box.height) / POINTS_PER_INCH
        except (AttributeError, ValueError, TypeError) as exc:
            raise PDFLoadError(f"page {index} has no usable MediaBox: {exc}") from None

        sizes: list[tuple[int, int]] = []
        count = 0
        if extract_images:
            try:
                for image in page.images:
                    count += 1
                    try:
                        sizes.append(tuple(image.image.size))
                    except Exception:  # noqa: BLE001
                        # The XObject is present but its pixels need a decoder
                        # we do not have. Counting it is still true; claiming
                        # its size would not be.
                        pass
            except Exception as exc:  # noqa: BLE001
                if not not_checked:
                    not_checked.append(
                        f"embedded image resolution — the reader could not "
                        f"extract images ({exc}); install Pillow for image "
                        "extraction"
                    )

        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""

        pages.append(
            PageInspection(
                index=index,
                width_in=width_in,
                height_in=height_in,
                image_count=count,
                image_sizes=tuple(sizes),
                has_text=bool(text.strip()),
            )
        )

    not_checked.append(
        "colour space and embedded font subsetting — KDP's own previewer is "
        "the authority on both and should be run before publishing"
    )

    return PDFInspection(
        page_count=len(pages),
        pages=tuple(pages),
        encrypted=encrypted,
        not_checked=tuple(not_checked),
    )
