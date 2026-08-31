"""A deterministic provider for tests and dry runs.

Draws synthetic **line art**, not noise: a white page with closed black
strokes, no midtones, nothing touching the edge. That matters because the
pixel inspection in ``kdp/inspection/image.py`` is real. A mock that emitted
random bytes would fail every check, and the end-to-end test would only be able
to prove the pipeline runs — not that a clean book passes it.

Two runs of the same book produce identical bytes, so the test can assert on
hashes, and each page's shapes derive from its prompt, so pages are genuinely
distinct under perceptual hashing.

It can also be told to fail, and to emit specific defects, because the paths
that reject a bad page need testing at least as much as the happy one.
"""

from __future__ import annotations

import hashlib
import struct
import zlib

from .base import GenerationRequest, GenerationResult


#: Luminance of paper and of ink. Nothing between the two is ever drawn: line
#: art has no anti-aliasing, and the inspection checks for exactly that.
PAPER = 255
INK = 0

#: Stroke width as a fraction of the shorter side, and the margin the drawing
#: is inset by so that no stroke reaches the page edge.
STROKE_FRACTION = 0.004
MARGIN_FRACTION = 0.08


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _encode_png(rows: list[bytearray], width: int, height: int) -> bytes:
    """Wrap raw greyscale rows as a PNG.

    Hand-rolled rather than drawn with Pillow so the mock has no dependencies
    and the core stays installable with nothing but the standard library.
    Filter type 0 throughout, and large flat areas, so a full print-resolution
    page compresses to a few kilobytes.
    """
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw.extend(row)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def _stroke_circle(
    rows: list[bytearray],
    cx: int,
    cy: int,
    radius: int,
    stroke: int,
    width: int,
    height: int,
    gap: int = 0,
) -> None:
    """Draw one circular outline by filling spans, not pixels.

    Span-filling keeps a print-resolution page cheap: the cost is proportional
    to the circumference, not the page area.

    ``gap`` leaves a break of that many rows in the right-hand side of the
    outline. That is the defect a real generator produces — a hairline the eye
    slides over on screen, through which a paint-bucket fill escapes across the
    whole page.
    """
    inner = max(0, radius - stroke)
    for dy in range(-radius, radius + 1):
        y = cy + dy
        if not 0 <= y < height:
            continue
        if gap and 0 <= dy < gap:
            # Skip the right-hand arc on these rows, leaving the outline open.
            outer_dx = int((radius * radius - dy * dy) ** 0.5)
            inner_dx = int((inner * inner - dy * dy) ** 0.5) if abs(dy) < inner else 0
            x0, x1 = max(0, cx - outer_dx), min(width, cx - inner_dx)
            if x1 > x0:
                rows[y][x0:x1] = bytes([INK]) * (x1 - x0)
            continue
        outer_dx = int((radius * radius - dy * dy) ** 0.5) if abs(dy) <= radius else 0
        row = rows[y]
        if abs(dy) >= inner:
            x0, x1 = max(0, cx - outer_dx), min(width, cx + outer_dx + 1)
            if x1 > x0:
                row[x0:x1] = bytes([INK]) * (x1 - x0)
        else:
            inner_dx = int((inner * inner - dy * dy) ** 0.5)
            for x0, x1 in (
                (cx - outer_dx, cx - inner_dx),
                (cx + inner_dx + 1, cx + outer_dx + 1),
            ):
                x0, x1 = max(0, x0), min(width, x1)
                if x1 > x0:
                    row[x0:x1] = bytes([INK]) * (x1 - x0)


def _stroke_rect(
    rows: list[bytearray], x0: int, y0: int, x1: int, y1: int, stroke: int, width: int, height: int
) -> None:
    """Draw one closed rectangular outline."""
    x0, x1 = max(0, x0), min(width, x1)
    y0, y1 = max(0, y0), min(height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    band = bytes([INK]) * (x1 - x0)
    for y in range(y0, min(y0 + stroke, y1)):
        rows[y][x0:x1] = band
    for y in range(max(y0, y1 - stroke), y1):
        rows[y][x0:x1] = band
    side = bytes([INK]) * stroke
    for y in range(y0, y1):
        rows[y][x0 : x0 + stroke] = side
        rows[y][max(x0, x1 - stroke) : x1] = side


def _line_art(
    width: int, height: int, seed: bytes, open_outlines: bool = False
) -> list[bytearray]:
    """A page of closed black outlines on white, deterministic in ``seed``.

    Shape count, kind, position and size all derive from the digest. Nothing is
    drawn on every page — an element common to all of them would dominate the
    perceptual hash and make every page read as a near-duplicate of every
    other, which is a defect in the fixture rather than in the book.
    """
    rows = [bytearray([PAPER]) * width for _ in range(height)]
    digest = hashlib.sha256(seed).digest()

    stroke = max(2, int(min(width, height) * STROKE_FRACTION))
    margin = int(min(width, height) * MARGIN_FRACTION)
    usable_w = width - 2 * margin
    usable_h = height - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        return rows

    # Tuned so the 24 fixture pages sit at least 14 bits apart under the
    # perceptual hash — comfortably clear of G4's threshold of 10 — while a
    # genuine rescaled repeat still lands within 1 bit.
    shape_count = 3 + (digest[0] % 6)
    span = min(usable_w, usable_h)

    for index in range(shape_count):
        base = (index * 5 + 1) % (len(digest) - 5)
        cx = margin + (digest[base] * usable_w) // 255
        cy = margin + (digest[base + 1] * usable_h) // 255
        size = max(stroke * 4, (span // 12) + (digest[base + 2] * span) // 420)

        # Clamp so the whole shape stays inside the margin: nothing may touch
        # the page edge, or the clipping check would fire on a clean page.
        size = min(
            size, cx - margin, cy - margin, width - margin - cx, height - margin - cy
        )
        if size <= stroke * 2:
            continue

        if digest[base + 3] % 2 or open_outlines:
            # Under open_outlines every shape is a circle, so every one of them
            # carries a break; a mixed page would leave some closed and make
            # the measurement ambiguous.
            gap = max(2, min(width, height) // 500) if open_outlines else 0
            _stroke_circle(rows, cx, cy, size, stroke, width, height, gap=gap)
        else:
            ratio = 60 + (digest[base + 4] % 80)
            half_w, half_h = size, (size * ratio) // 100
            half_h = min(half_h, cy - margin, height - margin - cy)
            if half_h > stroke:
                _stroke_rect(
                    rows,
                    cx - half_w,
                    cy - half_h,
                    cx + half_w,
                    cy + half_h,
                    stroke,
                    width,
                    height,
                )
    return rows


#: Defects the mock can inject on demand, so the paths that *reject* a page get
#: the same test coverage as the path that accepts one. Each maps to a real
#: failure that line art suffers from in practice.
DEFECTS = (
    "shading",
    "colour",
    "clipped",
    "blank",
    "solid",
    "watermark",
    "open_region",
)


def _apply_defect(
    rows: list[bytearray], defect: str, width: int, height: int
) -> tuple[list[bytearray], int]:
    """Damage a page in one specific way. Returns (rows, channels)."""
    if defect == "blank":
        # An empty page: the provider returned paper and nothing else.
        return [bytearray([PAPER]) * width for _ in range(height)], 1

    if defect == "solid":
        # A page flooded with ink: nothing left to colour.
        return [bytearray([INK]) * width for _ in range(height)], 1

    if defect == "shading":
        # Grey fill inside the drawing: the commonest line-art defect, and
        # invisible on screen until it prints as muddy grey.
        band = bytes([128]) * (width // 2)
        for y in range(height // 4, height // 2):
            rows[y][width // 4 : width // 4 + len(band)] = band
        return rows, 1

    if defect == "clipped":
        # A stroke running off the page edge.
        stroke = max(2, int(min(width, height) * STROKE_FRACTION))
        for y in range(height // 3, 2 * height // 3):
            rows[y][0:stroke] = bytes([INK]) * stroke
        return rows, 1

    if defect == "watermark":
        # A dense mark in one corner, the shape a signature makes.
        cw, ch = int(width * 0.10), int(height * 0.10)
        for y in range(height - ch, height):
            rows[y][width - cw : width] = bytes([INK]) * cw
        return rows, 1

    if defect == "colour":
        # Greyscale rows widened to RGB with one channel pushed, which is what
        # a provider ignoring "black and white only" produces.
        rgb: list[bytearray] = []
        for y, row in enumerate(rows):
            out = bytearray()
            tint = y > height // 2
            for value in row:
                if value == INK and tint:
                    out += bytes((200, 30, 30))
                else:
                    out += bytes((value, value, value))
            rgb.append(out)
        return rgb, 3

    raise ValueError(f"unknown defect {defect!r}; known defects: {', '.join(DEFECTS)}")


def _png(
    width: int, height: int, seed: bytes, defect: str | None = None
) -> bytes:
    """Line art as a PNG, optionally damaged in one specific way."""
    # open_region is drawn rather than applied afterwards: a break has to be in
    # the stroke itself, and no amount of post-processing a finished page
    # produces the same thing.
    rows = _line_art(width, height, seed, open_outlines=defect == "open_region")
    channels = 1
    if defect is not None and defect != "open_region":
        rows, channels = _apply_defect(rows, defect, width, height)

    if channels == 1:
        return _encode_png(rows, width, height)

    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw.extend(row)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


class MockImageProvider:
    """Deterministic, offline, and configurably unreliable."""

    name = "mock"

    def __init__(
        self,
        model: str = "mock-line-art-v1",
        fail_pages: tuple[str, ...] = (),
        #: Ceiling on the requested size. The default is above KDP's 300 DPI
        #: threshold for a full-page 8.5x11 image, so a mock-generated book
        #: passes the same resolution check a real one has to.
        max_dimension: int = 4000,
        #: page_id -> defect name, for exercising the rejection paths.
        defect_pages: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.fail_pages = tuple(fail_pages)
        self.max_dimension = max_dimension
        self.defect_pages = dict(defect_pages or {})

    def available(self) -> bool:
        return True

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.page_id in self.fail_pages:
            return GenerationResult(
                ok=False,
                page_id=request.page_id,
                provider=self.name,
                model=self.model,
                reason="mock provider was configured to fail this page",
            )

        width = min(request.width, self.max_dimension)
        height = min(request.height, self.max_dimension)
        seed = f"{request.prompt}|{request.attempt}".encode("utf-8")
        defect = self.defect_pages.get(request.page_id)
        return GenerationResult(
            ok=True,
            page_id=request.page_id,
            data=_png(width, height, seed, defect=defect),
            provider=self.name,
            model=self.model,
            width=width,
            height=height,
            file_format="png",
            meta={"deterministic": True, "defect": defect},
        )
