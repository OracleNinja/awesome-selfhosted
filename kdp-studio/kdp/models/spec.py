"""The book specification: what the generator is asked to make.

The spec is the contract between the concept stage and everything downstream.
It is pure data — no artwork, no prose — so it can be reviewed, diffed and
approved before a single page is generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ModelError
from ..specs import DEFAULT_PAPER, DEFAULT_TRIM


@dataclass(frozen=True, slots=True)
class PageSpec:
    """One interior page, described before it exists."""

    page_id: str
    #: Ordinal within the interior, 1-based.
    index: int
    #: What this page is: "art", "title", "copyright", "blank", "back_matter".
    role: str
    #: Original subject in our own words. Never a reference to another book.
    subject: str = ""
    #: 1 (simplest) to 5 (most intricate). Drives the difficulty curve.
    complexity: int = 3

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ModelError(f"page {self.page_id!r} has a non-positive index")
        if not 1 <= self.complexity <= 5:
            raise ModelError(
                f"page {self.page_id!r} has complexity {self.complexity}; "
                "the scale is 1-5"
            )

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "index": self.index,
            "role": self.role,
            "subject": self.subject,
            "complexity": self.complexity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PageSpec:
        try:
            return cls(
                page_id=data["page_id"],
                index=data["index"],
                role=data["role"],
                subject=data.get("subject", ""),
                complexity=data.get("complexity", 3),
            )
        except KeyError as exc:
            raise ModelError(f"page spec missing field {exc.args[0]!r}") from None


@dataclass(frozen=True, slots=True)
class BookSpec:
    """Everything needed to generate and manufacture one book."""

    book_id: str
    title: str
    subtitle: str = ""
    trim: str = DEFAULT_TRIM
    paper: str = DEFAULT_PAPER
    bleed: bool = True
    #: Total interior pages, including front and back matter.
    page_count: int = 0
    #: Coloring-book convention: art on one side only, so markers cannot bleed
    #: through onto a second drawing.
    single_sided_art: bool = True
    pages: tuple[PageSpec, ...] = field(default_factory=tuple)
    #: Provenance of the concept itself, carried into the manifest.
    derived_from_brief: str | None = None

    def __post_init__(self) -> None:
        if not self.book_id:
            raise ModelError("book_id must not be empty")
        if not self.title.strip():
            raise ModelError(f"book {self.book_id!r} has an empty title")

    @property
    def art_pages(self) -> tuple[PageSpec, ...]:
        return tuple(p for p in self.pages if p.role == "art")

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "trim": self.trim,
            "paper": self.paper,
            "bleed": self.bleed,
            "page_count": self.page_count,
            "single_sided_art": self.single_sided_art,
            "pages": [p.to_dict() for p in self.pages],
            "derived_from_brief": self.derived_from_brief,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BookSpec:
        try:
            return cls(
                book_id=data["book_id"],
                title=data["title"],
                subtitle=data.get("subtitle", ""),
                trim=data.get("trim", DEFAULT_TRIM),
                paper=data.get("paper", DEFAULT_PAPER),
                bleed=data.get("bleed", True),
                page_count=data.get("page_count", 0),
                single_sided_art=data.get("single_sided_art", True),
                pages=tuple(PageSpec.from_dict(p) for p in data.get("pages", ())),
                derived_from_brief=data.get("derived_from_brief"),
            )
        except KeyError as exc:
            raise ModelError(f"book spec missing field {exc.args[0]!r}") from None


@dataclass(frozen=True, slots=True)
class BookMetadata:
    """The KDP listing fields, kept separate from the book itself.

    Metadata changes far more often than a book does — retitling, keyword
    experiments, category moves — and keeping it out of ``BookSpec`` means
    those edits do not invalidate the interior's provenance.
    """

    book_id: str
    title: str
    subtitle: str = ""
    author: str = ""
    description: str = ""
    #: KDP accepts seven keyword slots.
    keywords: tuple[str, ...] = field(default_factory=tuple)
    #: KDP browse categories.
    categories: tuple[str, ...] = field(default_factory=tuple)
    language: str = "english"
    #: True only when a human has confirmed the disclosure answer on the form.
    ai_disclosure_confirmed: bool = False

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author,
            "description": self.description,
            "keywords": list(self.keywords),
            "categories": list(self.categories),
            "language": self.language,
            "ai_disclosure_confirmed": self.ai_disclosure_confirmed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BookMetadata:
        try:
            return cls(
                book_id=data["book_id"],
                title=data["title"],
                subtitle=data.get("subtitle", ""),
                author=data.get("author", ""),
                description=data.get("description", ""),
                keywords=tuple(data.get("keywords", ())),
                categories=tuple(data.get("categories", ())),
                language=data.get("language", "english"),
                ai_disclosure_confirmed=data.get("ai_disclosure_confirmed", False),
            )
        except KeyError as exc:
            raise ModelError(f"metadata missing field {exc.args[0]!r}") from None
