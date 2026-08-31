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
    SPINE_TEXT_CUTOFF_PAGES,
    InteriorGeometry,
    gutter_in,
    interior_geometry,
)
from .paper import DEFAULT_PAPER, PAPERS, Paper, get_paper
from .revision import (
    SOURCES,
    SPEC_REVISION,
    VERIFICATION,
    VERIFICATION_NOTE,
    VERIFICATION_TTL_DAYS,
    VERIFIED_ON,
    SourceRecord,
    is_verified,
    source,
    stale_tables,
    unverified_tables,
    verification_problem,
)
from .trim import (
    DEFAULT_TRIM,
    LARGE_TRIM_MAX_HEIGHT_IN,
    LARGE_TRIM_MAX_WIDTH_IN,
    TRIM_SIZES,
    TrimSize,
    get_trim,
    trims_by_cost_class,
)

__all__ = [
    "BLEED_IN",
    "DEFAULT_PAPER",
    "DEFAULT_TRIM",
    "GUTTER_BANDS",
    "MIN_IMAGE_DPI",
    "LARGE_TRIM_MAX_HEIGHT_IN",
    "LARGE_TRIM_MAX_WIDTH_IN",
    "MIN_PAGES_FOR_SPINE_TEXT",
    "SPINE_TEXT_CUTOFF_PAGES",
    "PAPERS",
    "SOURCES",
    "SPEC_REVISION",
    "TRIM_SIZES",
    "VERIFICATION",
    "VERIFICATION_NOTE",
    "VERIFICATION_TTL_DAYS",
    "VERIFIED_ON",
    "CoverGeometry",
    "InteriorGeometry",
    "Paper",
    "SourceRecord",
    "TrimSize",
    "cover_geometry",
    "get_paper",
    "get_trim",
    "gutter_in",
    "interior_geometry",
    "is_verified",
    "trims_by_cost_class",
    "source",
    "stale_tables",
    "unverified_tables",
    "verification_problem",
]
