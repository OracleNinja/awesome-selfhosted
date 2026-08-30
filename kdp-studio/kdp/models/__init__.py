"""Data models for every stage artefact.

All models are frozen dataclasses with explicit ``from_dict``/``to_dict``.
Validation happens in constructors, so an object that exists is an object that
is structurally sound; the gates then judge whether it is *acceptable*.
"""

from __future__ import annotations

from .manifest import MANIFEST_VERSION, BookManifest
from .provenance import (
    AIRole,
    AssetKind,
    AssetProvenance,
    DisclosureAnswer,
    content_hash,
    hash_file,
    roll_up_disclosure,
)
from .research import (
    FORBIDDEN_REFERENCE_MARKERS,
    MAX_OBSERVATION_CHARS,
    MAX_OBSERVATIONS,
    MarketSignal,
    ResearchBrief,
)
from .spec import BookMetadata, BookSpec, PageSpec

__all__ = [
    "FORBIDDEN_REFERENCE_MARKERS",
    "MANIFEST_VERSION",
    "MAX_OBSERVATIONS",
    "MAX_OBSERVATION_CHARS",
    "AIRole",
    "AssetKind",
    "AssetProvenance",
    "BookManifest",
    "BookMetadata",
    "BookSpec",
    "DisclosureAnswer",
    "MarketSignal",
    "PageSpec",
    "ResearchBrief",
    "content_hash",
    "hash_file",
    "roll_up_disclosure",
]
