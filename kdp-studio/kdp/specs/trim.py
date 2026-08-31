"""Trim sizes.

``standard`` marks the sizes KDP prints without a distribution surcharge and
that are available across its marketplaces. Non-standard sizes are legal but
narrow a book's distribution, so the spec gate reports them rather than
silently accepting them.

``cost_class`` says whether KDP prices a size as regular or large trim, which
changes the printing cost per copy — for an 8.5x11 book, by 54 cents on every
single copy.

It is *derived* from KDP's own rule rather than transcribed per size: large
trim is anything more than 6.12 inches wide or more than 9 inches high. Deriving
it means a size added later is classified correctly instead of defaulting to
whatever someone typed, and the test suite asserts the derivation against KDP's
explicit published list, so the rule and the table have to agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..errors import SpecError

#: KDP prices a trim as *large* when it exceeds either of these. Both bounds
#: are exclusive: 6x9 is regular, 6.14x9.21 is large.
LARGE_TRIM_MAX_WIDTH_IN: Final[float] = 6.12
LARGE_TRIM_MAX_HEIGHT_IN: Final[float] = 9.0


@dataclass(frozen=True, slots=True)
class TrimSize:
    """A finished page size, in inches."""

    key: str
    width_in: float
    height_in: float
    #: Whether the size distributes without a surcharge. NOT verified against
    #: KDP — the 2026-08-31 check covered the trim list and its cost classes,
    #: not expanded-distribution eligibility. None means unverified.
    standard: bool | None
    note: str = ""

    @property
    def cost_class(self) -> str:
        """"regular" or "large", by KDP's own definition of large trim."""
        return (
            "large"
            if self.width_in > LARGE_TRIM_MAX_WIDTH_IN
            or self.height_in > LARGE_TRIM_MAX_HEIGHT_IN
            else "regular"
        )

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
        TrimSize("8.25x6", 8.25, 6.0, None, "Landscape."),
        TrimSize("8.25x8.25", 8.25, 8.25, None, "Square."),
        TrimSize("8.5x8.5", 8.5, 8.5, None, "Square."),
        TrimSize("8.27x11.69", 8.27, 11.69, None, "A4."),
    )
}

#: The default trim for a coloring book.
DEFAULT_TRIM: Final[str] = "8.5x11"


def trims_by_cost_class(cost_class: str) -> tuple[str, ...]:
    return tuple(sorted(k for k, t in TRIM_SIZES.items() if t.cost_class == cost_class))


def get_trim(key: str) -> TrimSize:
    try:
        return TRIM_SIZES[key]
    except KeyError:
        known = ", ".join(sorted(TRIM_SIZES))
        raise SpecError(f"unknown trim size {key!r}; known sizes: {known}") from None
