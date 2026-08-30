"""Asset provenance and the KDP AI-disclosure roll-up.

KDP draws a line between two things that sound similar and are not:

* **AI-generated** — text, images or translations *produced* by an AI tool.
  Editing the output afterwards does not change this. Must be disclosed.
* **AI-assisted** — brainstorming, grammar checking, refining text a human
  wrote, improving an image a human made. Does not require disclosure.

The whole disclosure answer is a roll-up over per-asset records, which means
the answer is only as good as the records. So the taxonomy has a fourth value,
``UNKNOWN``, and it is the default. An asset nobody classified is not treated
as "probably fine" — it blocks the book. Under-disclosing risks the listing and
the account; over-disclosing costs nothing, since KDP states the answer is
internal and affects neither royalties nor ranking. The asymmetry is the whole
reason this module fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..errors import ModelError


class AIRole(str, Enum):
    """How an AI tool contributed to one asset."""

    #: No AI involvement at all.
    NONE = "none"
    #: AI helped a human make it. Not disclosable under KDP's definition.
    ASSISTED = "assisted"
    #: AI produced it. Disclosable, even if a human edited it afterwards.
    GENERATED = "generated"
    #: Not yet classified. Blocks the book; never rolls up to "no disclosure".
    UNKNOWN = "unknown"

    @property
    def requires_disclosure(self) -> bool:
        return self is AIRole.GENERATED

    @property
    def is_classified(self) -> bool:
        return self is not AIRole.UNKNOWN


class AssetKind(str, Enum):
    """The KDP disclosure categories, plus the ones that fall outside them.

    KDP asks about text, images and translations specifically. ``METADATA`` and
    ``OTHER`` are tracked for our own audit trail but are reported separately
    so they are never mistaken for a disclosure category.
    """

    TEXT = "text"
    IMAGE = "image"
    TRANSLATION = "translation"
    METADATA = "metadata"
    OTHER = "other"

    @property
    def is_disclosure_category(self) -> bool:
        return self in (AssetKind.TEXT, AssetKind.IMAGE, AssetKind.TRANSLATION)


def content_hash(data: bytes) -> str:
    """Stable content address for an asset. Used for dedupe and for manifests."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path: str | Path) -> str:
    """Content-hash a file without reading it all into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AssetProvenance:
    """Where one asset came from and what made it.

    ``tool`` and ``model`` are required whenever an AI had any hand in the
    asset, because "AI-generated" with no record of which tool produced it is
    not an audit trail, it is a note to self.
    """

    asset_id: str
    kind: AssetKind
    ai_role: AIRole
    content_hash: str
    #: Identifier of the producing tool or process, e.g. a model id, or the
    #: name of a deterministic generator for non-AI assets.
    tool: str | None = None
    #: Prompt version, mirroring the operation registry pattern used in
    #: embroidery-genie-ai's AI gateway, so a prompt edit is visible here.
    prompt_version: str | None = None
    #: Free-form human note. Never used by any gate decision.
    note: str = ""
    #: Assets this one was derived from, by asset_id.
    derived_from: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ModelError("asset_id must not be empty")
        if not self.content_hash.startswith("sha256:"):
            raise ModelError(
                f"content_hash for {self.asset_id!r} must be a 'sha256:' address"
            )
        if self.ai_role in (AIRole.ASSISTED, AIRole.GENERATED) and not self.tool:
            raise ModelError(
                f"asset {self.asset_id!r} claims ai_role={self.ai_role.value!r} "
                "but names no tool; an unattributed AI claim is not an audit trail"
            )

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind.value,
            "ai_role": self.ai_role.value,
            "content_hash": self.content_hash,
            "tool": self.tool,
            "prompt_version": self.prompt_version,
            "note": self.note,
            "derived_from": list(self.derived_from),
        }

    @classmethod
    def from_dict(cls, data: dict) -> AssetProvenance:
        try:
            return cls(
                asset_id=data["asset_id"],
                kind=AssetKind(data.get("kind", AssetKind.OTHER.value)),
                ai_role=AIRole(data.get("ai_role", AIRole.UNKNOWN.value)),
                content_hash=data["content_hash"],
                tool=data.get("tool"),
                prompt_version=data.get("prompt_version"),
                note=data.get("note", ""),
                derived_from=tuple(data.get("derived_from", ())),
            )
        except KeyError as exc:
            raise ModelError(f"provenance record missing field {exc.args[0]!r}") from None
        except ValueError as exc:
            raise ModelError(f"provenance record has an invalid value: {exc}") from None


@dataclass(frozen=True, slots=True)
class DisclosureAnswer:
    """The roll-up that answers KDP's AI-content question.

    ``complete`` is the gate-relevant field. When it is False the other fields
    are provisional and must not be copied into a KDP submission form.
    """

    #: True when at least one asset is AI-generated.
    required: bool
    #: Which of KDP's three categories contain AI-generated assets.
    categories: tuple[str, ...]
    #: True when every asset has been classified.
    complete: bool
    #: asset_ids still carrying AIRole.UNKNOWN.
    unclassified: tuple[str, ...]
    #: asset_ids that drive the disclosure, for the audit trail.
    generated_assets: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "required": self.required,
            "categories": list(self.categories),
            "complete": self.complete,
            "unclassified": list(self.unclassified),
            "generated_assets": list(self.generated_assets),
        }

    def statement(self) -> str:
        """One line a human can read before ticking the box on KDP."""
        if not self.complete:
            return (
                f"INCOMPLETE — {len(self.unclassified)} asset(s) unclassified. "
                "Do not answer the KDP disclosure question yet."
            )
        if not self.required:
            return "No AI-generated content. KDP AI-content disclosure not required."
        cats = ", ".join(self.categories)
        return f"Contains AI-generated content. Disclose to KDP for: {cats}."


def roll_up_disclosure(records: list[AssetProvenance]) -> DisclosureAnswer:
    """Derive the disclosure answer from per-asset provenance.

    Fails closed: any unclassified asset makes the answer incomplete, whatever
    the other records say.
    """
    unclassified = tuple(
        sorted(r.asset_id for r in records if not r.ai_role.is_classified)
    )
    generated = [r for r in records if r.ai_role.requires_disclosure]
    categories = tuple(
        sorted({r.kind.value for r in generated if r.kind.is_disclosure_category})
    )
    return DisclosureAnswer(
        required=bool(generated),
        categories=categories,
        complete=not unclassified,
        unclassified=unclassified,
        generated_assets=tuple(sorted(r.asset_id for r in generated)),
    )
