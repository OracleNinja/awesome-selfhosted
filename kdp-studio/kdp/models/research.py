"""Market research records, and the guardrail that keeps them research.

Competitor books are studied for *market* signals: what sells, at what price,
at what page count, what reviewers complain about. None of that requires
keeping any of a competitor's creative expression, and keeping it is how a
research corpus quietly turns into a copying risk.

So the record type is a whitelist, not a free-form bag. A ``MarketSignal``
can hold numbers, enumerated categories and short abstracted observations —
and structurally cannot hold artwork, page images, verbatim interior text, or
a reproduction of a competitor's layout. The research-hygiene gate enforces
the remaining soft constraints (length caps, no asset references) on top.

The rule this encodes: observe the market, describe it in your own abstracted
terms, and never carry the expression across.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ModelError

#: Free-text observations are capped hard. A sentence of characterisation is a
#: market signal; a paragraph starts to be a place where copied text can hide.
MAX_OBSERVATION_CHARS: int = 240

#: Cap on how many observations one signal may carry, for the same reason.
MAX_OBSERVATIONS: int = 12

#: Substrings that indicate someone stored a pointer to competitor creative
#: material rather than an abstracted signal.
FORBIDDEN_REFERENCE_MARKERS: tuple[str, ...] = (
    "data:image",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".pdf",
    ".svg",
)


@dataclass(frozen=True, slots=True)
class MarketSignal:
    """One abstracted observation about a competing product.

    Deliberately absent: cover images, interior scans, page text, title/author
    strings of specific competitors, and anything else that would let this
    record reconstruct someone else's book.
    """

    #: An opaque local handle. Never an ASIN, title or author — the corpus does
    #: not need to identify whose book this was in order to be useful.
    signal_id: str
    #: Broad market bucket, e.g. "adult-mandala", "kids-animals-ages-4-8".
    segment: str
    #: Observed list price, in USD, rounded to the dollar. None when unknown.
    price_usd: float | None = None
    #: Observed page count. None when unknown.
    page_count: int | None = None
    #: Observed trim size key, when it maps onto a known trim.
    trim: str | None = None
    #: Coarse popularity band rather than a scraped figure: one of
    #: "low", "medium", "high".
    demand_band: str | None = None
    #: Short abstracted observations in our own words, e.g.
    #: "reviewers repeatedly ask for single-sided pages".
    observations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.signal_id:
            raise ModelError("signal_id must not be empty")
        if self.demand_band not in (None, "low", "medium", "high"):
            raise ModelError(
                f"demand_band must be low/medium/high or None, got {self.demand_band!r}"
            )
        if len(self.observations) > MAX_OBSERVATIONS:
            raise ModelError(
                f"signal {self.signal_id!r} carries {len(self.observations)} "
                f"observations; the cap is {MAX_OBSERVATIONS}"
            )

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "segment": self.segment,
            "price_usd": self.price_usd,
            "page_count": self.page_count,
            "trim": self.trim,
            "demand_band": self.demand_band,
            "observations": list(self.observations),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MarketSignal:
        unknown = set(data) - {
            "signal_id",
            "segment",
            "price_usd",
            "page_count",
            "trim",
            "demand_band",
            "observations",
        }
        if unknown:
            raise ModelError(
                f"market signal carries fields outside the research whitelist: "
                f"{', '.join(sorted(unknown))}. The whitelist is the guardrail "
                "that keeps competitor material out of the corpus."
            )
        try:
            return cls(
                signal_id=data["signal_id"],
                segment=data["segment"],
                price_usd=data.get("price_usd"),
                page_count=data.get("page_count"),
                trim=data.get("trim"),
                demand_band=data.get("demand_band"),
                observations=tuple(data.get("observations", ())),
            )
        except KeyError as exc:
            raise ModelError(f"market signal missing field {exc.args[0]!r}") from None


@dataclass(frozen=True, slots=True)
class ResearchBrief:
    """The output of the research stage: signals plus a chosen direction."""

    brief_id: str
    segment: str
    signals: tuple[MarketSignal, ...] = field(default_factory=tuple)
    #: The gap this book intends to fill, in our own words.
    opportunity: str = ""
    #: Recommended trim/page count, carried forward into the book spec.
    recommended_trim: str | None = None
    recommended_page_count: int | None = None

    def to_dict(self) -> dict:
        return {
            "brief_id": self.brief_id,
            "segment": self.segment,
            "signals": [s.to_dict() for s in self.signals],
            "opportunity": self.opportunity,
            "recommended_trim": self.recommended_trim,
            "recommended_page_count": self.recommended_page_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ResearchBrief:
        try:
            return cls(
                brief_id=data["brief_id"],
                segment=data["segment"],
                signals=tuple(
                    MarketSignal.from_dict(s) for s in data.get("signals", ())
                ),
                opportunity=data.get("opportunity", ""),
                recommended_trim=data.get("recommended_trim"),
                recommended_page_count=data.get("recommended_page_count"),
            )
        except KeyError as exc:
            raise ModelError(f"research brief missing field {exc.args[0]!r}") from None
