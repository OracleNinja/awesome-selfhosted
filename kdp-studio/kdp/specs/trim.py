"""Trim sizes.

``standard`` marks the sizes KDP prints without a distribution surcharge and
that are available across its marketplaces. Non-standard sizes are legal but
narrow a book's distribution, so the spec gate reports them rather than
silently accepting them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..errors import SpecError


@dataclass(frozen=True, slots=True)
class TrimSize:
    """A finished page size, in inches."""

    key: str
    width_in: float
    height_in: float
    standard: bool
    note: str = ""

    @property
    def label(self) -> str:
        return f'{self.width_in:g}" x {self.height_in:g}"'


TRIM_SIZES: Final[dict[str, TrimSize]] = {
    t.key: t
    for t in (
        TrimSize("5x8", 5.0, 8.0, True),
        TrimSize("5.06x7.81", 5.06, 7.81, True),
        TrimSize("5.25x8", 5.25, 8.0, True),
        TrimSize("5.5x8.5", 5.5, 8.5, True),
        TrimSize("6x9", 6.0, 9.0, True),
        TrimSize("6.14x9.21", 6.14, 9.21, True),
        TrimSize("6.69x9.61", 6.69, 9.61, True),
        TrimSize("7x10", 7.0, 10.0, True),
        TrimSize("7.44x9.69", 7.44, 9.69, True),
        TrimSize("7.5x9.25", 7.5, 9.25, True),
        TrimSize("8x10", 8.0, 10.0, True, "Common for children's activity books."),
        TrimSize("8.5x11", 8.5, 11.0, True, "The workhorse trim for coloring books."),
        TrimSize("8.25x6", 8.25, 6.0, False, "Landscape; non-standard."),
        TrimSize("8.5x8.5", 8.5, 8.5, False, "Square; non-standard."),
        TrimSize("8.27x11.69", 8.27, 11.69, False, "A4; non-standard."),
    )
}

#: The default trim for a coloring book.
DEFAULT_TRIM: Final[str] = "8.5x11"


def get_trim(key: str) -> TrimSize:
    try:
        return TRIM_SIZES[key]
    except KeyError:
        known = ", ".join(sorted(TRIM_SIZES))
        raise SpecError(f"unknown trim size {key!r}; known sizes: {known}") from None
