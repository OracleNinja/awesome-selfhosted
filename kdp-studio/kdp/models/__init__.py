"""Data models for every stage artefact.

All models are frozen dataclasses with explicit ``from_dict``/``to_dict``.
Validation happens in constructors, so an object that exists is structurally
sound; the gates then judge whether it is *acceptable*.
"""

from __future__ import annotations

from .approval import ApprovalRecord, ApprovalStatus, fingerprint
from .artifacts import MATERIAL_ARTIFACTS, Artifact, ArtifactKind
from .economics import (
    ASSUMED_FIXED_COST_USD,
    ASSUMED_PER_PAGE_COST_USD,
    ASSUMED_ROYALTY_RATE,
    Assumption,
    Economics,
    PricingScenario,
    compare,
    default_assumptions,
)
from .manifest import MANIFEST_VERSION, BookManifest
from .prompts import MIN_PROMPT_CHARS, AssetPrompt, PromptPlan
from .provenance import (
    AIRole,
    AssetKind,
    AssetProvenance,
    AssetStatus,
    DisclosureAnswer,
    Transformation,
    approved_versions,
    attempts_for,
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
from .runs import RunRecord
from .spec import BookMetadata, BookSpec, PageSpec
from .strategy import BookStrategy, Claim, Confidence

__all__ = [
    "ASSUMED_FIXED_COST_USD",
    "ASSUMED_PER_PAGE_COST_USD",
    "ASSUMED_ROYALTY_RATE",
    "FORBIDDEN_REFERENCE_MARKERS",
    "MANIFEST_VERSION",
    "MATERIAL_ARTIFACTS",
    "MAX_OBSERVATIONS",
    "MAX_OBSERVATION_CHARS",
    "MIN_PROMPT_CHARS",
    "AIRole",
    "ApprovalRecord",
    "ApprovalStatus",
    "Artifact",
    "ArtifactKind",
    "AssetKind",
    "AssetPrompt",
    "AssetProvenance",
    "AssetStatus",
    "Assumption",
    "BookManifest",
    "BookMetadata",
    "BookSpec",
    "BookStrategy",
    "Claim",
    "Confidence",
    "DisclosureAnswer",
    "Economics",
    "MarketSignal",
    "PageSpec",
    "PricingScenario",
    "PromptPlan",
    "ResearchBrief",
    "RunRecord",
    "Transformation",
    "approved_versions",
    "attempts_for",
    "compare",
    "content_hash",
    "default_assumptions",
    "fingerprint",
    "hash_file",
    "roll_up_disclosure",
]
