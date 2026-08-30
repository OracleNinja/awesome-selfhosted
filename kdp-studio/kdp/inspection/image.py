"""Pixel-level inspection of a line-art page.

What a coloring page has to be is unusually easy to state numerically: black
strokes on white, nothing in between, nothing coloured, nothing touching the
edge. That makes most of these checks genuinely decidable rather than
heuristic — a page with 12% of its pixels in the midtones really does have
shading in it, and no judgement call is involved.

Two checks are *not* decidable and are labelled as such rather than dressed up:

* **Text detection** needs OCR. There is no OCR here, so the inspection reports
  that it could not look rather than reporting that it found nothing. Those are
  different claims and only one of them is true.
* **Watermarks, logos and signatures** get a corner-ink heuristic — they
  usually sit in a corner — which is a *signal for review*, never a verdict.

Saying "not checked" costs a reviewer thirty seconds. Saying "checked, clean"
when nothing looked is how a watermarked page reaches print.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import KDPError
from ..imaging import RawImage, decode_png


class ImageLoadError(KDPError):
    """An image could not be read by any available decoder."""


# --- thresholds -------------------------------------------------------------
#
# Stated as named constants because a reviewer challenging a finding needs to
# see the number that produced it.

#: At or above this luminance a pixel counts as paper.
WHITE_THRESHOLD = 240
#: At or below this luminance a pixel counts as ink.
BLACK_THRESHOLD = 64
#: Anything between the two is a midtone — shading, anti-aliasing, or a grey
#: fill. Some is unavoidable at stroke edges; a lot means the page is shaded.
MAX_MIDTONE_FRACTION = 0.06

#: A page needs some ink to be a drawing, and not so much that it is a solid
#: block a child cannot colour.
MIN_INK_FRACTION = 0.005
MAX_INK_FRACTION = 0.60

#: The border band examined for a background that is not paper, expressed as a
#: fraction of the shorter side.
BORDER_BAND = 0.02
#: Ink anywhere in the outermost row/column means the drawing runs off the page.
MAX_EDGE_INK_FRACTION = 0.002

#: Colour tolerance. Line art is greyscale; a channel spread beyond this in a
#: meaningful number of pixels means colour got in.
MAX_CHANNEL_SPREAD = 24
MAX_COLOURED_FRACTION = 0.001

#: Corner boxes searched for a mark, as a fraction of each side.
CORNER_BAND = 0.12
#: Ink concentrated in a corner while the rest of the border is clean is the
#: shape a signature or watermark makes.
CORNER_INK_SUSPICION = 0.04


@dataclass(frozen=True, slots=True)
class ImageIssue:
    """One thing wrong with a page's pixels.

    ``blocking`` separates a defect that makes the page unusable from a signal
    that needs a human to look. The gate maps these onto its own severities;
    keeping the judgement here means the threshold and the verdict it produces
    sit next to each other.
    """

    code: str
    message: str
    blocking: bool = True
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImageInspection:
    """What the pixels say. Every field is measured, none is inferred."""

    width: int
    height: int
    channels: int
    #: Fraction of pixels at or above WHITE_THRESHOLD.
    white_fraction: float
    #: Fraction at or below BLACK_THRESHOLD.
    ink_fraction: float
    #: Fraction strictly between the two.
    midtone_fraction: float
    #: Fraction whose RGB channels differ by more than MAX_CHANNEL_SPREAD.
    coloured_fraction: float
    #: Mean luminance of the border band.
    border_mean: float
    #: Fraction of the outermost ring that is ink.
    edge_ink_fraction: float
    #: Per-corner ink fraction, clockwise from top-left.
    corner_ink: tuple[float, float, float, float]
    #: Checks that could not be performed, with the reason.
    not_checked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_greyscale(self) -> bool:
        return self.channels == 1 or self.coloured_fraction <= MAX_COLOURED_FRACTION

    @property
    def suspicious_corners(self) -> tuple[int, ...]:
        """Corners holding markedly more ink than the page edge generally."""
        return tuple(
            index
            for index, value in enumerate(self.corner_ink)
            if value >= CORNER_INK_SUSPICION
        )

    def effective_dpi(self, width_in: float, height_in: float) -> float:
        """Resolution when placed in a box of the given size, in inches."""
        if width_in <= 0 or height_in <= 0:
            return 0.0
        return min(self.width / width_in, self.height / height_in)

    def problems(self) -> tuple[ImageIssue, ...]:
        """Everything measurably wrong with this page."""
        issues: list[ImageIssue] = []

        if self.border_mean < WHITE_THRESHOLD:
            issues.append(
                ImageIssue(
                    "image.background_not_white",
                    f"Background is not paper: the border averages "
                    f"{self.border_mean:.0f} where {WHITE_THRESHOLD}+ is white. "
                    "A tinted or textured background prints as a grey wash.",
                    detail={"border_mean": round(self.border_mean, 1)},
                )
            )

        if self.midtone_fraction > MAX_MIDTONE_FRACTION:
            issues.append(
                ImageIssue(
                    "image.shading_present",
                    f"{self.midtone_fraction:.1%} of pixels are midtones, above "
                    f"the {MAX_MIDTONE_FRACTION:.0%} allowance. Line art should "
                    "be black on white; greys print muddy and cannot be "
                    "coloured over.",
                    detail={"midtone_fraction": round(self.midtone_fraction, 4)},
                )
            )

        if self.ink_fraction < MIN_INK_FRACTION:
            issues.append(
                ImageIssue(
                    "image.no_drawing",
                    f"Only {self.ink_fraction:.2%} of the page is ink. There is "
                    "effectively nothing on it.",
                    detail={"ink_fraction": round(self.ink_fraction, 5)},
                )
            )
        elif self.ink_fraction > MAX_INK_FRACTION:
            issues.append(
                ImageIssue(
                    "image.too_much_ink",
                    f"{self.ink_fraction:.0%} of the page is ink, above the "
                    f"{MAX_INK_FRACTION:.0%} ceiling. There is little left to "
                    "colour, and it is expensive to print.",
                    detail={"ink_fraction": round(self.ink_fraction, 4)},
                )
            )

        if self.coloured_fraction > MAX_COLOURED_FRACTION:
            issues.append(
                ImageIssue(
                    "image.colour_present",
                    f"{self.coloured_fraction:.2%} of pixels carry colour. The "
                    "interior prints in black ink, so colour is either lost or "
                    "rendered as grey.",
                    detail={"coloured_fraction": round(self.coloured_fraction, 5)},
                )
            )

        if self.edge_ink_fraction > MAX_EDGE_INK_FRACTION:
            issues.append(
                ImageIssue(
                    "image.clipped_at_edge",
                    f"{self.edge_ink_fraction:.1%} of the outermost ring is ink, "
                    "so the drawing runs off the page and will be cut by the "
                    "trim.",
                    detail={"edge_ink_fraction": round(self.edge_ink_fraction, 4)},
                )
            )

        for index in self.suspicious_corners:
            corner = ("top-left", "top-right", "bottom-right", "bottom-left")[index]
            issues.append(
                ImageIssue(
                    "image.possible_mark",
                    f"Ink is concentrated in the {corner} corner "
                    f"({self.corner_ink[index]:.0%} of it). That is the shape a "
                    "watermark, logo or signature makes — look at the page. This "
                    "is a signal for review, not a determination.",
                    blocking=False,
                    detail={"corner": corner, "ink": round(self.corner_ink[index], 4)},
                )
            )

        return tuple(issues)

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "white_fraction": round(self.white_fraction, 5),
            "ink_fraction": round(self.ink_fraction, 5),
            "midtone_fraction": round(self.midtone_fraction, 5),
            "coloured_fraction": round(self.coloured_fraction, 5),
            "border_mean": round(self.border_mean, 2),
            "edge_ink_fraction": round(self.edge_ink_fraction, 5),
            "corner_ink": [round(c, 5) for c in self.corner_ink],
            "not_checked": list(self.not_checked),
        }


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def load_image(source: str | Path | bytes) -> RawImage:
    """Read an image into raw pixels.

    Tries the standard-library PNG decoder first — it needs nothing installed
    and handles what the pipeline itself produces — then falls back to Pillow
    for everything else. Only when both fail is the artefact genuinely
    uninspectable, and that is when a gate is entitled to block.
    """
    data = Path(source).read_bytes() if isinstance(source, (str, Path)) else source

    stdlib_error: Exception | None = None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            return decode_png(data)
        except Exception as exc:  # noqa: BLE001 - fall through to Pillow
            stdlib_error = exc

    if not _pillow_available():
        detail = f" (stdlib PNG decoder: {stdlib_error})" if stdlib_error else ""
        raise ImageLoadError(
            "cannot read this image: it is not a PNG the standard-library "
            f"decoder handles{detail}, and Pillow is not installed. Install the "
            "imaging extras (pip install -r requirements.txt)."
        )

    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as handle:
            converted = handle.convert("L" if handle.mode in ("L", "1", "LA") else "RGB")
            channels = 1 if converted.mode == "L" else 3
            return RawImage(
                width=converted.width,
                height=converted.height,
                channels=channels,
                pixels=converted.tobytes(),
            )
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageLoadError(f"Pillow could not decode this image: {exc}") from None


def _luminance_rows(image: RawImage) -> list[bytes]:
    """One bytes object per row, each byte a luminance value."""
    if image.channels == 1:
        stride = image.width
        return [
            image.pixels[y * stride : (y + 1) * stride] for y in range(image.height)
        ]

    stride = image.width * image.channels
    rows = []
    for y in range(image.height):
        row = image.pixels[y * stride : (y + 1) * stride]
        # Rec. 601 luma, integer arithmetic: keeps this exact and fast without
        # pulling in numpy.
        rows.append(
            bytes(
                (row[x * 3] * 299 + row[x * 3 + 1] * 587 + row[x * 3 + 2] * 114) // 1000
                for x in range(image.width)
            )
        )
    return rows


def _region_ink(rows: list[bytes], x0: int, y0: int, x1: int, y1: int) -> float:
    total = 0
    ink = 0
    for y in range(y0, y1):
        row = rows[y]
        for x in range(x0, x1):
            total += 1
            if row[x] <= BLACK_THRESHOLD:
                ink += 1
    return ink / total if total else 0.0


def inspect_image(source: str | Path | bytes) -> ImageInspection:
    """Measure a page. Raises :class:`ImageLoadError` if it cannot be read."""
    image = load_image(source)
    if image.width == 0 or image.height == 0:
        raise ImageLoadError("image has a zero dimension")

    rows = _luminance_rows(image)
    total = image.width * image.height

    white = ink = midtone = 0
    for row in rows:
        for value in row:
            if value >= WHITE_THRESHOLD:
                white += 1
            elif value <= BLACK_THRESHOLD:
                ink += 1
            else:
                midtone += 1

    # Colour, only meaningful for a multi-channel image.
    coloured = 0
    if image.channels == 3:
        pixels = image.pixels
        for offset in range(0, len(pixels), 3):
            trio = pixels[offset : offset + 3]
            if max(trio) - min(trio) > MAX_CHANNEL_SPREAD:
                coloured += 1

    # Border band: a background that is not paper shows up here first.
    band = max(1, int(min(image.width, image.height) * BORDER_BAND))
    border_values = []
    for y in range(min(band, image.height)):
        border_values.extend(rows[y])
    for y in range(max(0, image.height - band), image.height):
        border_values.extend(rows[y])
    for y in range(image.height):
        row = rows[y]
        border_values.extend(row[:band])
        border_values.extend(row[max(0, image.width - band) :])
    border_mean = sum(border_values) / len(border_values) if border_values else 255.0

    # Outermost ring only: ink here means the drawing is clipped by the edge.
    edge_total = edge_ink = 0
    for x in range(image.width):
        for row in (rows[0], rows[-1]):
            edge_total += 1
            if row[x] <= BLACK_THRESHOLD:
                edge_ink += 1
    for y in range(image.height):
        for x in (0, image.width - 1):
            edge_total += 1
            if rows[y][x] <= BLACK_THRESHOLD:
                edge_ink += 1

    cw = max(1, int(image.width * CORNER_BAND))
    ch = max(1, int(image.height * CORNER_BAND))
    corners = (
        _region_ink(rows, 0, 0, cw, ch),
        _region_ink(rows, image.width - cw, 0, image.width, ch),
        _region_ink(rows, image.width - cw, image.height - ch, image.width, image.height),
        _region_ink(rows, 0, image.height - ch, cw, image.height),
    )

    not_checked = (
        "accidental text — requires OCR, which is not installed; no text "
        "detection was attempted",
    )

    return ImageInspection(
        width=image.width,
        height=image.height,
        channels=image.channels,
        white_fraction=white / total,
        ink_fraction=ink / total,
        midtone_fraction=midtone / total,
        coloured_fraction=coloured / total if image.channels == 3 else 0.0,
        border_mean=border_mean,
        edge_ink_fraction=edge_ink / edge_total if edge_total else 0.0,
        corner_ink=corners,
        not_checked=not_checked,
    )


#: Side of the grid the perceptual hash reduces an image to. 8 gives a 64-bit
#: hash over 8x9 samples, which is the usual dhash trade-off.
DHASH_SIZE = 8


def perceptual_hash(source: str | Path | bytes) -> int:
    """A difference hash: robust to rescaling and mild edits, not to content.

    Two images whose hashes differ in only a few bits are near-duplicates —
    the same drawing mirrored, recoloured, or nudged. That is a *signal*, and
    the gate that uses it says so; it is not a copyright determination.
    """
    image = load_image(source)
    rows = _luminance_rows(image)

    # Box-sample down to (DHASH_SIZE + 1) x DHASH_SIZE, then compare each
    # sample with its right-hand neighbour.
    grid: list[list[int]] = []
    for gy in range(DHASH_SIZE):
        y0 = gy * image.height // DHASH_SIZE
        y1 = max(y0 + 1, (gy + 1) * image.height // DHASH_SIZE)
        line = []
        for gx in range(DHASH_SIZE + 1):
            x0 = gx * image.width // (DHASH_SIZE + 1)
            x1 = max(x0 + 1, (gx + 1) * image.width // (DHASH_SIZE + 1))
            total = count = 0
            for y in range(y0, min(y1, image.height)):
                row = rows[y]
                for x in range(x0, min(x1, image.width)):
                    total += row[x]
                    count += 1
            line.append(total // count if count else 0)
        grid.append(line)

    bits = 0
    for line in grid:
        for gx in range(DHASH_SIZE):
            bits = (bits << 1) | int(line[gx] > line[gx + 1])
    return bits


def hamming(a: int, b: int) -> int:
    """Bit distance between two perceptual hashes."""
    return bin(a ^ b).count("1")
