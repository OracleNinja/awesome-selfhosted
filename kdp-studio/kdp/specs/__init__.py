"""Versioned KDP print specifications and the geometry derived from them.

Everything in here is a pure function of the tables in this package. No
network, no clock, no filesystem — the same inputs always produce the same
numbers, which is what lets a gate result be re-checked months later.
"""

from __future__ import annotations

from .cover import CoverGeometry, cover_geometry
from .interior import (
    BLEED_IN,
    GUTTER_BANDS,
    MIN_IMAGE_DPI,
    MIN_PAGES_FOR_SPINE_TEXT,
    InteriorGeometry,
    gutter_in,
    interior_geometry,
)
from .paper import DEFAULT_PAPER, PAPERS, Paper, get_paper
from .revision import (
    SPEC_REVISION,
    VERIFICATION,
    VERIFICATION_NOTE,
    VERIFIED_ON,
    is_verified,
)
from .trim import DEFAULT_TRIM, TRIM_SIZES, TrimSize, get_trim

__all__ = [
    "BLEED_IN",
    "DEFAULT_PAPER",
    "DEFAULT_TRIM",
    "GUTTER_BANDS",
    "MIN_IMAGE_DPI",
    "MIN_PAGES_FOR_SPINE_TEXT",
    "PAPERS",
    "SPEC_REVISION",
    "TRIM_SIZES",
    "VERIFICATION",
    "VERIFICATION_NOTE",
    "VERIFIED_ON",
    "CoverGeometry",
    "InteriorGeometry",
    "Paper",
    "TrimSize",
    "cover_geometry",
    "get_paper",
    "get_trim",
    "gutter_in",
    "interior_geometry",
    "is_verified",
]
