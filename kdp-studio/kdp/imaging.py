"""Minimal PNG decoding, standard library only.

Enough to get raw pixel rows out of a PNG so they can be embedded in a PDF.
Not a general image library — it handles the 8-bit greyscale and truecolour
forms that line art comes in, and refuses anything else loudly rather than
producing a corrupted page.

Writing this instead of depending on Pillow keeps the whole pipeline runnable
with nothing installed, which is the property the gates rely on: a check that
cannot run must be able to say so, and that is hard if the code that would say
it also fails to import.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .errors import KDPError


class ImageError(KDPError):
    """An image could not be decoded."""


#: Bytes per pixel by PNG colour type. None marks a form we do not handle.
_CHANNELS = {0: 1, 2: 3, 3: None, 4: 2, 6: 4}


@dataclass(frozen=True, slots=True)
class RawImage:
    """Decoded pixels, one row after another, no filter bytes."""

    width: int
    height: int
    #: 1 for greyscale, 3 for RGB.
    channels: int
    #: width * height * channels bytes.
    pixels: bytes

    @property
    def colour_space(self) -> str:
        return "DeviceGray" if self.channels == 1 else "DeviceRGB"


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(data: bytes, width: int, height: int, bpp: int) -> bytes:
    """Reverse PNG's per-row filters. The five types are defined in RFC 2083."""
    stride = width * bpp
    out = bytearray()
    previous = bytearray(stride)
    pos = 0

    for row in range(height):
        if pos >= len(data):
            raise ImageError(f"PNG data ends after {row} of {height} rows")
        filter_type = data[pos]
        pos += 1
        line = bytearray(data[pos : pos + stride])
        if len(line) < stride:
            raise ImageError(f"PNG row {row} is short: {len(line)} of {stride} bytes")
        pos += stride

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upper_left = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper_left)) & 0xFF
        else:
            raise ImageError(f"unknown PNG filter type {filter_type} on row {row}")

        out += line
        previous = line

    return bytes(out)


def decode_png(data: bytes) -> RawImage:
    """Decode a PNG into raw pixel rows."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageError("not a PNG file")

    pos = 8
    header: tuple[int, int, int, int, int] | None = None
    idat = bytearray()

    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # length + tag + payload + crc

        if tag == b"IHDR":
            width, height, depth, colour, compression, filt, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            header = (width, height, depth, colour, interlace)
            if depth != 8:
                raise ImageError(
                    f"only 8-bit PNGs are supported; this one is {depth}-bit"
                )
            if interlace:
                raise ImageError("interlaced PNGs are not supported")
            if _CHANNELS.get(colour) is None:
                raise ImageError(f"unsupported PNG colour type {colour}")
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break

    if header is None:
        raise ImageError("PNG has no IHDR chunk")
    if not idat:
        raise ImageError("PNG has no image data")

    width, height, _, colour, _ = header
    bpp = _CHANNELS[colour]
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ImageError(f"PNG image data will not decompress: {exc}") from None

    pixels = _unfilter(raw, width, height, bpp)

    # Drop alpha: a coloring page has no transparency to preserve, and PDF
    # would need a separate soft mask for it.
    if colour in (4, 6):
        keep = 1 if colour == 4 else 3
        stride = bpp
        pixels = bytes(
            b
            for i, b in enumerate(pixels)
            if (i % stride) < keep
        )
        bpp = keep

    return RawImage(width=width, height=height, channels=bpp, pixels=pixels)
