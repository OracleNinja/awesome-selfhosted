"""Unit handling for the embroidery pipeline.

Every internal coordinate is stored in *tenths of a millimetre* (0.1 mm), which
is the native resolution of every commercial embroidery format we export
(DST, PES/PEC, JEF, EXP, VP3, HUS, XXX).  Keeping one canonical internal unit
removes an entire class of rounding bugs when a design round-trips between
formats.
"""

from __future__ import annotations

# Canonical internal unit: 0.1 mm
TENTHS_PER_MM = 10.0
MM_PER_INCH = 25.4

# PES/PEC and JEF both quantise to 0.1 mm as well, so no scaling is needed.
DEFAULT_DPI = 96.0


def mm_to_units(mm: float) -> float:
    return mm * TENTHS_PER_MM


def units_to_mm(units: float) -> float:
    return units / TENTHS_PER_MM


def inch_to_units(inch: float) -> float:
    return inch * MM_PER_INCH * TENTHS_PER_MM


def units_to_inch(units: float) -> float:
    return units / TENTHS_PER_MM / MM_PER_INCH


def px_to_units(px: float, dpi: float = DEFAULT_DPI) -> float:
    """Convert screen/SVG pixels to internal units at a given DPI."""
    return px / dpi * MM_PER_INCH * TENTHS_PER_MM


def units_to_px(units: float, dpi: float = DEFAULT_DPI) -> float:
    return units / TENTHS_PER_MM / MM_PER_INCH * dpi


def parse_length(value: str | float | int, dpi: float = DEFAULT_DPI) -> float:
    """Parse an SVG length (``12``, ``12px``, ``4mm``, ``1in``) into units."""
    if isinstance(value, (int, float)):
        return px_to_units(float(value), dpi)
    text = str(value).strip().lower()
    for suffix, fn in (
        ("mm", lambda v: mm_to_units(v)),
        ("cm", lambda v: mm_to_units(v * 10.0)),
        ("in", lambda v: inch_to_units(v)),
        ("pt", lambda v: px_to_units(v * dpi / 72.0, dpi)),
        ("pc", lambda v: px_to_units(v * dpi / 6.0, dpi)),
        ("px", lambda v: px_to_units(v, dpi)),
    ):
        if text.endswith(suffix):
            return fn(float(text[: -len(suffix)] or 0))
    if text.endswith("%"):
        # Percentages are resolved by the caller against a viewport; treat the
        # bare number as pixels so we never explode on malformed input.
        return px_to_units(float(text[:-1] or 0), dpi)
    return px_to_units(float(text or 0), dpi)
