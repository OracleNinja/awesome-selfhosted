"""Versioned prompts, one per page.

A single vague prompt reused for a whole book produces a whole book of the same
drawing. Each page gets its own prompt, and each prompt is versioned, so that
when page 14 comes back with stray shading the fix is a new prompt version for
page 14 — traceable, and not silently applied to the other twenty-three pages.

``prompt_hash`` is the deterministic identity of the *text actually sent*. Two
prompts with the same hash are the same prompt regardless of how they were
assembled, which is what makes a repeated-prompt check meaningful.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..errors import ModelError

#: A prompt shorter than this is not specifying enough to produce a consistent
#: book. The number is a floor against "a cat, line art".
MIN_PROMPT_CHARS: int = 80


@dataclass(frozen=True, slots=True)
class AssetPrompt:
    """The generation brief for one page."""

    prompt_id: str
    page_id: str
    version: str
    subject: str
    #: What the drawing should show and how it should be arranged.
    composition: str = ""
    #: Who it is for.
    audience: str = ""
    #: Line weight, closure, stroke consistency.
    line_art_requirements: str = ""
    #: What the background must be — for a coloring book, pure white.
    background_requirements: str = ""
    #: 1 (simplest) to 5 (most intricate). Mirrors PageSpec.complexity.
    complexity: int = 3
    #: Rules shared across the book so pages look like one set.
    consistency_requirements: str = ""
    #: Things that must not appear: colour, shading, text, watermarks,
    #: signatures, recognisable characters, trademarks.
    prohibited: tuple[str, ...] = field(default_factory=tuple)
    #: Requested output geometry.
    aspect_ratio: str = ""
    width: int | None = None
    height: int | None = None
    #: Provider-specific knobs. Never credentials.
    generation_constraints: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompt_id:
            raise ModelError("prompt_id must not be empty")
        if not self.page_id:
            raise ModelError(f"prompt {self.prompt_id!r} names no page")
        if not self.version:
            raise ModelError(f"prompt {self.prompt_id!r} has no version")
        if not 1 <= self.complexity <= 5:
            raise ModelError(
                f"prompt {self.prompt_id!r} has complexity {self.complexity}; "
                "the scale is 1-5"
            )

    def render(self) -> str:
        """The full prompt, prohibitions included.

        Deterministic: the same prompt object always renders the same string,
        so its hash is a stable identity. Providers that take a negative prompt
        separately should use ``render_positive`` and ``render_negative``
        instead — this form is what a provider without one has to send.
        """
        parts = [self.render_positive()]
        if self.prohibited:
            parts.append("Must not contain: " + "; ".join(self.prohibited))
        return "\n".join(parts)

    def render_positive(self) -> str:
        """What the image should be, with nothing about what it should not be."""
        parts = [f"Subject: {self.subject}"]
        for label, value in (
            ("Composition", self.composition),
            ("Audience", self.audience),
            ("Line art", self.line_art_requirements),
            ("Background", self.background_requirements),
            ("Consistency", self.consistency_requirements),
            ("Aspect ratio", self.aspect_ratio),
        ):
            if value:
                parts.append(f"{label}: {value}")
        parts.append(f"Complexity: {self.complexity}/5")
        return "\n".join(parts)

    def render_negative(self) -> str:
        """What must not appear, for providers that take it separately."""
        return ", ".join(self.prohibited)

    @property
    def prompt_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.render().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "prompt_id": self.prompt_id,
            "page_id": self.page_id,
            "version": self.version,
            "subject": self.subject,
            "composition": self.composition,
            "audience": self.audience,
            "line_art_requirements": self.line_art_requirements,
            "background_requirements": self.background_requirements,
            "complexity": self.complexity,
            "consistency_requirements": self.consistency_requirements,
            "prohibited": list(self.prohibited),
            "aspect_ratio": self.aspect_ratio,
            "width": self.width,
            "height": self.height,
            "generation_constraints": self.generation_constraints,
            # Derived, written out for the audit trail and re-derived on read.
            "prompt_hash": self.prompt_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AssetPrompt:
        try:
            return cls(
                prompt_id=data["prompt_id"],
                page_id=data["page_id"],
                version=data["version"],
                subject=data.get("subject", ""),
                composition=data.get("composition", ""),
                audience=data.get("audience", ""),
                line_art_requirements=data.get("line_art_requirements", ""),
                background_requirements=data.get("background_requirements", ""),
                complexity=data.get("complexity", 3),
                consistency_requirements=data.get("consistency_requirements", ""),
                prohibited=tuple(data.get("prohibited", ())),
                aspect_ratio=data.get("aspect_ratio", ""),
                width=data.get("width"),
                height=data.get("height"),
                generation_constraints=data.get("generation_constraints", {}),
            )
        except KeyError as exc:
            raise ModelError(f"prompt missing field {exc.args[0]!r}") from None


@dataclass(frozen=True, slots=True)
class PromptPlan:
    """Every prompt for a book, plus the rules shared across them."""

    plan_id: str
    #: Rules every page inherits, stated once.
    house_style: str = ""
    prompts: tuple[AssetPrompt, ...] = field(default_factory=tuple)

    def for_page(self, page_id: str) -> AssetPrompt | None:
        for prompt in self.prompts:
            if prompt.page_id == page_id:
                return prompt
        return None

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "house_style": self.house_style,
            "prompts": [p.to_dict() for p in self.prompts],
        }

    @classmethod
    def from_dict(cls, data: dict) -> PromptPlan:
        try:
            return cls(
                plan_id=data["plan_id"],
                house_style=data.get("house_style", ""),
                prompts=tuple(
                    AssetPrompt.from_dict(p) for p in data.get("prompts", ())
                ),
            )
        except KeyError as exc:
            raise ModelError(f"prompt plan missing field {exc.args[0]!r}") from None
