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
from .approval import ApprovalRecord, fingerprint
from .artifacts import MATERIAL_ARTIFACTS, Artifact, ArtifactKind
from .economics import Economics
from .prompts import PromptPlan
from .provenance import (
    AssetProvenance,
    DisclosureAnswer,
    approved_versions,
    attempts_for,
    roll_up_disclosure,
)
from .research import ResearchBrief
from .runs import RunRecord
from .spec import BookMetadata, BookSpec
from .strategy import BookStrategy

#: Bumped when the manifest layout changes incompatibly. Revision 2 adds
#: strategy, prompt plan, economics, artefacts, approval and the run log, and
#: turns ``assets`` into a full attempt history rather than a flat list of
#: shipping assets. Revision 1 manifests still load: an asset with no explicit
#: status is read as approved, which is what it meant.
MANIFEST_VERSION: str = "2"


@dataclass(frozen=True, slots=True)
class BookManifest:
    """Everything known about one book, in one serialisable record."""

    spec: BookSpec
    #: Every attempt at every asset, never pruned. Shipping versions are a
    #: filter over this list rather than a second structure.
    assets: tuple[AssetProvenance, ...] = field(default_factory=tuple)
    metadata: BookMetadata | None = None
    #: Set by the pipeline as stages complete.
    stage: str = "research"
    #: Spec revision the geometry was computed under.
    spec_revision: str = SPEC_REVISION
    manifest_version: str = MANIFEST_VERSION
    research: ResearchBrief | None = None
    strategy: BookStrategy | None = None
    prompt_plan: PromptPlan | None = None
    economics: Economics | None = None
    artifacts: tuple[Artifact, ...] = field(default_factory=tuple)
    approval: ApprovalRecord = field(default_factory=ApprovalRecord)
    #: Append-only execution log.
    runs: tuple[RunRecord, ...] = field(default_factory=tuple)

    @property
    def book_id(self) -> str:
        return self.spec.book_id

    # -- assets ------------------------------------------------------------

    @property
    def approved_assets(self) -> tuple[AssetProvenance, ...]:
        """The versions that ship."""
        return approved_versions(list(self.assets))

    def attempts(self, page_id: str) -> tuple[AssetProvenance, ...]:
        """Every attempt at one page, oldest first."""
        return attempts_for(list(self.assets), page_id)

    def approved_for(self, page_id: str) -> AssetProvenance | None:
        for record in self.approved_assets:
            if record.page == page_id:
                return record
        return None

    def disclosure(self) -> DisclosureAnswer:
        """Derive the KDP AI-content answer from the versions that ship."""
        return roll_up_disclosure(list(self.assets))

    def asset(self, asset_id: str) -> AssetProvenance | None:
        for record in self.assets:
            if record.asset_id == asset_id:
                return record
        return None

    # -- artefacts ---------------------------------------------------------

    def artifact(self, kind: ArtifactKind) -> Artifact | None:
        for item in self.artifacts:
            if item.kind is kind:
                return item
        return None

    # -- approval ----------------------------------------------------------

    def production_fingerprint(self) -> str:
        """A hash over everything a reviewer signs off on.

        Deliberately narrow: the shipping assets, the built interior and cover,
        the listing metadata and the chosen price. Changing a run record or a
        note does not invalidate an approval; changing the book does.
        """
        selected = self.economics.selected_scenario if self.economics else None
        return fingerprint(
            {
                "book_id": self.book_id,
                "spec": self.spec.to_dict(),
                "assets": [
                    {"page": a.page, "hash": a.content_hash}
                    for a in self.approved_assets
                ],
                "artifacts": {
                    item.kind.value: item.content_hash
                    for item in self.artifacts
                    if item.kind in MATERIAL_ARTIFACTS
                },
                "metadata": self.metadata.to_dict() if self.metadata else None,
                "price": selected.to_dict() if selected else None,
            }
        )

    def approval_is_current(self) -> bool:
        """True when the recorded approval describes the book as it stands."""
        return self.approval.is_valid_for(self.production_fingerprint())

    # -- transitions -------------------------------------------------------

    def with_assets(self, assets: tuple[AssetProvenance, ...]) -> BookManifest:
        return replace(self, assets=assets)

    def with_stage(self, stage: str) -> BookManifest:
        return replace(self, stage=stage)

    def with_artifact(self, artifact: Artifact) -> BookManifest:
        """Replace any artefact of the same kind, keeping the rest."""
        others = tuple(a for a in self.artifacts if a.kind is not artifact.kind)
        return replace(self, artifacts=others + (artifact,))

    def with_run(self, run: RunRecord) -> BookManifest:
        return replace(self, runs=self.runs + (run,))

    def to_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "spec_revision": self.spec_revision,
            "stage": self.stage,
            "spec": self.spec.to_dict(),
            "assets": [a.to_dict() for a in self.assets],
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "research": self.research.to_dict() if self.research else None,
            "strategy": self.strategy.to_dict() if self.strategy else None,
            "prompt_plan": self.prompt_plan.to_dict() if self.prompt_plan else None,
            "economics": self.economics.to_dict() if self.economics else None,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "approval": self.approval.to_dict(),
            "runs": [r.to_dict() for r in self.runs],
            # Derived, and written out so a reader does not have to recompute
            # them. Never read back in — from_dict re-derives both from the
            # underlying records, so hand-editing either block changes nothing
            # a gate sees.
            "disclosure": self.disclosure().to_dict(),
            "production_fingerprint": self.production_fingerprint(),
            "approval_is_current": self.approval_is_current(),
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
                research=(
                    ResearchBrief.from_dict(data["research"])
                    if data.get("research")
                    else None
                ),
                strategy=(
                    BookStrategy.from_dict(data["strategy"])
                    if data.get("strategy")
                    else None
                ),
                prompt_plan=(
                    PromptPlan.from_dict(data["prompt_plan"])
                    if data.get("prompt_plan")
                    else None
                ),
                economics=(
                    Economics.from_dict(data["economics"])
                    if data.get("economics")
                    else None
                ),
                artifacts=tuple(
                    Artifact.from_dict(a) for a in data.get("artifacts", ())
                ),
                approval=ApprovalRecord.from_dict(data.get("approval", {})),
                runs=tuple(RunRecord.from_dict(r) for r in data.get("runs", ())),
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
