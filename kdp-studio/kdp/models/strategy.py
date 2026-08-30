"""The book strategy: what to build, and why it is not a clone.

Sits between research (what the market looks like) and the specification (what
this book is). Its job is to make differentiation an explicit, checkable claim
rather than an assumption nobody wrote down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..errors import ModelError


class Confidence(str, Enum):
    """How a market claim is known.

    The distinction exists because estimated sales presented as verified sales
    is the single most misleading thing a research artefact can do. A number
    with no confidence label is treated as ``UNKNOWN``, never as observed.
    """

    #: Directly seen — a listed price, a stated page count.
    OBSERVED = "observed"
    #: Reasoned from something observed.
    INFERRED = "inferred"
    #: A modelled guess. Never presentable as fact.
    ESTIMATED = "estimated"
    #: Not known. The honest default.
    UNKNOWN = "unknown"

    @property
    def is_evidence(self) -> bool:
        """True only for claims that rest on something actually seen."""
        return self in (Confidence.OBSERVED, Confidence.INFERRED)


@dataclass(frozen=True, slots=True)
class Claim:
    """One market claim, with how it is known and where it came from."""

    label: str
    value: str
    confidence: Confidence = Confidence.UNKNOWN
    basis: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "value": self.value,
            "confidence": self.confidence.value,
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Claim:
        try:
            return cls(
                label=data["label"],
                value=str(data["value"]),
                confidence=Confidence(data.get("confidence", Confidence.UNKNOWN.value)),
                basis=data.get("basis", ""),
            )
        except KeyError as exc:
            raise ModelError(f"claim missing field {exc.args[0]!r}") from None
        except ValueError as exc:
            raise ModelError(f"claim has an invalid confidence: {exc}") from None


@dataclass(frozen=True, slots=True)
class BookStrategy:
    """The decision to build this particular book."""

    strategy_id: str
    #: The research brief this came from.
    brief_id: str
    niche: str
    audience: str
    #: e.g. "ages 4-8", or "" where age is not meaningful.
    age_range: str = ""
    #: The concept in one or two sentences, in our own words.
    concept: str = ""
    #: What makes this book different from what already sells. This is the
    #: field G7 judges; a book that cannot articulate it is a clone.
    unique_angle: str = ""
    #: Why a reader benefits: entertainment, practice, calm, learning.
    purpose: str = ""
    #: Concrete differentiators, each a specific decision rather than an
    #: adjective. "Single-sided pages" counts; "higher quality" does not.
    differentiators: tuple[str, ...] = field(default_factory=tuple)
    #: Named risks: saturation, trademark proximity, seasonality.
    risks: tuple[str, ...] = field(default_factory=tuple)
    #: Market observations carried forward, each with its confidence.
    claims: tuple[Claim, ...] = field(default_factory=tuple)
    #: Candidate titles/subtitles/keywords/categories. Concepts, not final
    #: copy — the metadata stage owns the listing.
    title_concepts: tuple[str, ...] = field(default_factory=tuple)
    subtitle_concepts: tuple[str, ...] = field(default_factory=tuple)
    keyword_concepts: tuple[str, ...] = field(default_factory=tuple)
    category_concepts: tuple[str, ...] = field(default_factory=tuple)
    #: Production direction handed to the concept stage.
    recommended_trim: str | None = None
    recommended_page_count: int | None = None
    visual_direction: str = ""

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ModelError("strategy_id must not be empty")

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "brief_id": self.brief_id,
            "niche": self.niche,
            "audience": self.audience,
            "age_range": self.age_range,
            "concept": self.concept,
            "unique_angle": self.unique_angle,
            "purpose": self.purpose,
            "differentiators": list(self.differentiators),
            "risks": list(self.risks),
            "claims": [c.to_dict() for c in self.claims],
            "title_concepts": list(self.title_concepts),
            "subtitle_concepts": list(self.subtitle_concepts),
            "keyword_concepts": list(self.keyword_concepts),
            "category_concepts": list(self.category_concepts),
            "recommended_trim": self.recommended_trim,
            "recommended_page_count": self.recommended_page_count,
            "visual_direction": self.visual_direction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BookStrategy:
        try:
            return cls(
                strategy_id=data["strategy_id"],
                brief_id=data.get("brief_id", ""),
                niche=data.get("niche", ""),
                audience=data.get("audience", ""),
                age_range=data.get("age_range", ""),
                concept=data.get("concept", ""),
                unique_angle=data.get("unique_angle", ""),
                purpose=data.get("purpose", ""),
                differentiators=tuple(data.get("differentiators", ())),
                risks=tuple(data.get("risks", ())),
                claims=tuple(Claim.from_dict(c) for c in data.get("claims", ())),
                title_concepts=tuple(data.get("title_concepts", ())),
                subtitle_concepts=tuple(data.get("subtitle_concepts", ())),
                keyword_concepts=tuple(data.get("keyword_concepts", ())),
                category_concepts=tuple(data.get("category_concepts", ())),
                recommended_trim=data.get("recommended_trim"),
                recommended_page_count=data.get("recommended_page_count"),
                visual_direction=data.get("visual_direction", ""),
            )
        except KeyError as exc:
            raise ModelError(f"strategy missing field {exc.args[0]!r}") from None
