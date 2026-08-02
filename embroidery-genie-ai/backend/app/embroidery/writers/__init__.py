"""Format writers.

``write(pattern, "dst") -> bytes`` is the only entry point callers need.
Routing: pyembroidery when installed (broadest and best-tested coverage),
otherwise our native writers for the formats we own outright.
"""

from __future__ import annotations

import logging

from ..pattern import Command, EmbroideryPattern
from . import dst as _dst  # noqa: F401  (registers the native writer)
from . import exp as _exp  # noqa: F401
from .base import (
    EXPORTABLE,
    FORMATS,
    FormatSpec,
    UnsupportedFormatError,
    native_writer,
    supported_formats,
)

log = logging.getLogger(__name__)

try:  # pragma: no cover - exercised by the presence/absence of the dependency
    import pyembroidery

    HAS_PYEMBROIDERY = True
except ImportError:  # pragma: no cover
    pyembroidery = None  # type: ignore[assignment]
    HAS_PYEMBROIDERY = False


_PYEMB_COMMAND = {
    Command.STITCH: "STITCH",
    Command.JUMP: "JUMP",
    Command.TRIM: "TRIM",
    Command.COLOR_CHANGE: "COLOR_CHANGE",
    Command.STOP: "STOP",
    Command.END: "END",
}


def to_pyembroidery(pattern: EmbroideryPattern):
    """Convert our pattern into a ``pyembroidery.EmbPattern``.

    Coordinates match: both use 0.1 mm units with Y increasing downward, so no
    transform is applied here — the individual format writers handle their own
    axis conventions.
    """
    if not HAS_PYEMBROIDERY:  # pragma: no cover
        raise RuntimeError("pyembroidery is not installed")

    emb = pyembroidery.EmbPattern()
    for index, block in enumerate(pattern.blocks):
        if index > 0:
            emb.color_change()
        thread = pyembroidery.EmbThread()
        thread.set_color(block.thread.r, block.thread.g, block.thread.b)
        thread.description = block.thread.name or block.thread.hex
        thread.catalog_number = block.thread.code
        thread.brand = block.thread.catalog
        emb.add_thread(thread)

        for stitch in block.stitches:
            if stitch.command == Command.JUMP:
                emb.add_stitch_absolute(pyembroidery.JUMP, stitch.x, stitch.y)
            elif stitch.command == Command.TRIM:
                emb.add_stitch_absolute(pyembroidery.TRIM, stitch.x, stitch.y)
            elif stitch.command == Command.STOP:
                emb.add_stitch_absolute(pyembroidery.STOP, stitch.x, stitch.y)
            else:
                emb.add_stitch_absolute(pyembroidery.STITCH, stitch.x, stitch.y)

    emb.end()
    emb.metadata("name", pattern.name)
    return emb


def _write_with_pyembroidery(pattern: EmbroideryPattern, extension: str) -> bytes:
    # pyembroidery's writers seek and re-read, so they need a real file rather
    # than a stream.
    import os
    import tempfile

    emb = to_pyembroidery(pattern)
    handle, path = tempfile.mkstemp(suffix=f".{extension}")
    os.close(handle)
    try:
        pyembroidery.write(emb, path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover
            pass


def write(pattern: EmbroideryPattern, extension: str, prefer_native: bool = False) -> bytes:
    """Serialise a pattern to the given format, returning raw bytes."""
    key = extension.lower().lstrip(".")
    spec = FORMATS.get(key)
    if spec is None:
        raise UnsupportedFormatError(
            f"Unknown format '{extension}'. Supported: {', '.join(FORMATS)}"
        )

    if key not in EXPORTABLE:
        raise UnsupportedFormatError(
            f".{key} cannot be written: {spec.notes} "
            f"Exportable formats: {', '.join(sorted(EXPORTABLE))}."
        )

    fn = native_writer(key)
    if fn and (prefer_native or not HAS_PYEMBROIDERY):
        return fn(pattern)

    if HAS_PYEMBROIDERY:
        try:
            return _write_with_pyembroidery(pattern, key)
        except Exception as exc:  # pragma: no cover - backend failure path
            log.warning("pyembroidery failed writing .%s (%s); trying native writer", key, exc)
            if fn:
                return fn(pattern)
            raise

    if fn:
        return fn(pattern)

    raise UnsupportedFormatError(
        f".{key} requires the 'pyembroidery' package. Install it with "
        "`pip install pyembroidery` or export to DST/EXP instead."
    )


def available_formats() -> list[dict]:
    """Formats this deployment can actually produce right now."""
    out = []
    for spec in FORMATS.values():
        if spec.key not in EXPORTABLE:
            backend = "unavailable"
        elif spec.native and not HAS_PYEMBROIDERY:
            backend = "native"
        elif HAS_PYEMBROIDERY:
            backend = "pyembroidery"
        elif spec.native:
            backend = "native"
        else:
            backend = "unavailable"
        out.append(
            {
                "extension": spec.extension,
                "name": spec.name,
                "vendor": spec.vendor,
                "available": backend != "unavailable",
                "backend": backend,
                "notes": spec.notes,
            }
        )
    return out


__all__ = [
    "EXPORTABLE",
    "FORMATS",
    "FormatSpec",
    "HAS_PYEMBROIDERY",
    "UnsupportedFormatError",
    "available_formats",
    "supported_formats",
    "to_pyembroidery",
    "write",
]
