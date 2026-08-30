"""Built artefacts: the files a stage produces.

Each is content-addressed, so "the interior that was approved" is a hash rather
than a path — a path can be overwritten in place, and a hash cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..errors import ModelError
from .provenance import hash_file


class ArtifactKind(str, Enum):
    INTERIOR_PDF = "interior_pdf"
    COVER_PDF = "cover_pdf"
    CONTACT_SHEET = "contact_sheet"
    REVIEW_PACKAGE = "review_package"
    PUBLICATION_PACKAGE = "publication_package"


#: The artefacts a reviewer signs off on. Changing any of these after approval
#: invalidates it — see kdp.models.approval.
MATERIAL_ARTIFACTS: tuple[ArtifactKind, ...] = (
    ArtifactKind.INTERIOR_PDF,
    ArtifactKind.COVER_PDF,
)


@dataclass(frozen=True, slots=True)
class Artifact:
    """One produced file."""

    kind: ArtifactKind
    path: str
    content_hash: str
    created_at: str | None = None
    bytes_size: int | None = None

    def __post_init__(self) -> None:
        if not self.content_hash.startswith("sha256:"):
            raise ModelError(
                f"artifact {self.kind.value} must carry a 'sha256:' content hash"
            )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "bytes_size": self.bytes_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Artifact:
        try:
            return cls(
                kind=ArtifactKind(data["kind"]),
                path=data["path"],
                content_hash=data["content_hash"],
                created_at=data.get("created_at"),
                bytes_size=data.get("bytes_size"),
            )
        except KeyError as exc:
            raise ModelError(f"artifact missing field {exc.args[0]!r}") from None
        except ValueError as exc:
            raise ModelError(f"artifact has an invalid kind: {exc}") from None

    @classmethod
    def from_file(
        cls, kind: ArtifactKind, path: str | Path, created_at: str | None = None
    ) -> Artifact:
        target = Path(path)
        return cls(
            kind=kind,
            path=str(path),
            content_hash=hash_file(target),
            created_at=created_at,
            bytes_size=target.stat().st_size,
        )
