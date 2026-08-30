"""A deterministic provider for tests and dry runs.

Produces a distinct, reproducible byte string per page from the prompt hash, so
the whole pipeline can be exercised end to end without a paid service and
without network access. Two runs of the same book produce identical bytes,
which is what lets the end-to-end test assert on hashes.

It can also be told to fail, because the failure path needs testing at least as
much as the success path.
"""

from __future__ import annotations

import hashlib
import struct
import zlib

from .base import GenerationRequest, GenerationResult


def _png(width: int, height: int, seed: bytes) -> bytes:
    """A minimal valid greyscale PNG whose pixels derive from ``seed``.

    Hand-rolled rather than drawn with Pillow so the mock has no dependencies
    and the core stays installable with nothing but the standard library.
    """

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # One byte per pixel, 8-bit greyscale, each row preceded by its filter
    # byte (type 0, no filtering).
    #
    # Rows are flat runs rather than per-pixel noise. That keeps a full
    # print-resolution page — 2400x3000 is 7.2 MB raw — compressing to a few
    # kilobytes, so the end-to-end test can exercise real geometry and real DPI
    # checks without writing hundreds of megabytes of fixtures.
    noise = hashlib.sha256(seed).digest()
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows.extend(bytes([noise[y % len(noise)]]) * width)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
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
    ) -> None:
        self.model = model
        self.fail_pages = tuple(fail_pages)
        self.max_dimension = max_dimension

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
        return GenerationResult(
            ok=True,
            page_id=request.page_id,
            data=_png(width, height, seed),
            provider=self.name,
            model=self.model,
            width=width,
            height=height,
            file_format="png",
            meta={"deterministic": True},
        )
