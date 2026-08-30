"""The book manifest: the single auditable record of what a book is.

One JSON file per book, carrying the spec, every asset's provenance, the
metadata, the disclosure roll-up and the spec revision the numbers were
computed under. It is the artefact that makes the pipeline auditable rather
than merely automated — six months from now it still explains why a given
book passed a given gate.

Serialisation is canonical: sorted keys, no insignificant whitespace. Two
runs that produce the same book produce byte-identical manifests, so a diff
means something really changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..errors import ModelError
from ..specs import SPEC_REVISION
from .provenance import AssetProvenance, DisclosureAnswer, roll_up_disclosure
from .spec import BookMetadata, BookSpec

#: Bumped when the manifest layout changes incompatibly.
MANIFEST_VERSION: str = "1"


@dataclass(frozen=True, slots=True)
class BookManifest:
    """Everything known about one book, in one serialisable record."""

    spec: BookSpec
    assets: tuple[AssetProvenance, ...] = field(default_factory=tuple)
    metadata: BookMetadata | None = None
    #: Set by the pipeline as stages complete.
    stage: str = "research"
    #: Spec revision the geometry was computed under.
    spec_revision: str = SPEC_REVISION
    manifest_version: str = MANIFEST_VERSION

    @property
    def book_id(self) -> str:
        return self.spec.book_id

    def disclosure(self) -> DisclosureAnswer:
        """Derive the KDP AI-content answer from the asset records."""
        return roll_up_disclosure(list(self.assets))

    def asset(self, asset_id: str) -> AssetProvenance | None:
        for record in self.assets:
            if record.asset_id == asset_id:
                return record
        return None

    def with_assets(self, assets: tuple[AssetProvenance, ...]) -> BookManifest:
        return replace(self, assets=assets)

    def with_stage(self, stage: str) -> BookManifest:
        return replace(self, stage=stage)

    def to_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "spec_revision": self.spec_revision,
            "stage": self.stage,
            "spec": self.spec.to_dict(),
            "assets": [a.to_dict() for a in self.assets],
            "metadata": self.metadata.to_dict() if self.metadata else None,
            # Derived, and written out so a reader does not have to recompute
            # it. Never read back in — from_dict re-derives it from the assets,
            # so a hand-edited disclosure block cannot lie to a gate.
            "disclosure": self.disclosure().to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> BookManifest:
        try:
            return cls(
                spec=BookSpec.from_dict(data["spec"]),
                assets=tuple(
                    AssetProvenance.from_dict(a) for a in data.get("assets", ())
                ),
                metadata=(
                    BookMetadata.from_dict(data["metadata"])
                    if data.get("metadata")
                    else None
                ),
                stage=data.get("stage", "research"),
                spec_revision=data.get("spec_revision", SPEC_REVISION),
                manifest_version=data.get("manifest_version", MANIFEST_VERSION),
            )
        except KeyError as exc:
            raise ModelError(f"manifest missing field {exc.args[0]!r}") from None

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, compact separators, trailing newline."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return target

    @classmethod
    def read(cls, path: str | Path) -> BookManifest:
        raw = Path(path).read_text(encoding="utf-8")
        try:
            return cls.from_dict(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ModelError(f"manifest at {path} is not valid JSON: {exc}") from None
