"""Human approval, and the fingerprint that makes it honest.

Approval is not a boolean. It is a signed statement about a *specific* book:
these assets, this interior, this cover, this metadata, this price. If any of
those change afterwards, the approval no longer describes what would be
published, so it stops being valid.

That is enforced by fingerprint rather than by trust. The approval records a
hash over the material artefacts; the check recomputes it. A book edited after
sign-off fails the comparison and needs a new approval — there is no way to
edit the interior and keep the tick.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from ..errors import ModelError


class ApprovalStatus(str, Enum):
    #: Never reviewed.
    PENDING = "pending"
    #: A human approved this exact book.
    APPROVED = "approved"
    #: A human reviewed it and said no.
    REJECTED = "rejected"

    @property
    def permits_packaging(self) -> bool:
        return self is ApprovalStatus.APPROVED


def fingerprint(components: dict) -> str:
    """A stable hash over whatever a reviewer signed off on.

    Canonical JSON, so key order and formatting cannot change the fingerprint
    while the content stays the same.
    """
    payload = json.dumps(components, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """One human decision about one exact version of a book."""

    status: ApprovalStatus = ApprovalStatus.PENDING
    #: Who decided. A name or handle, never a credential.
    reviewer: str | None = None
    #: ISO-8601 UTC timestamp of the decision.
    decided_at: str | None = None
    #: The fingerprint of the book as it stood when the decision was made.
    book_fingerprint: str | None = None
    #: What was in front of the reviewer, for the audit trail.
    approved_artifacts: tuple[str, ...] = field(default_factory=tuple)
    #: Free-form reviewer notes, including the reason for a rejection.
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status is not ApprovalStatus.PENDING:
            if not self.reviewer:
                raise ModelError(
                    f"an approval decision of {self.status.value!r} names no "
                    "reviewer; an anonymous sign-off is not a sign-off"
                )
            if not self.book_fingerprint:
                raise ModelError(
                    f"an approval decision of {self.status.value!r} records no "
                    "book fingerprint, so it cannot be tied to what was reviewed"
                )

    def is_valid_for(self, current_fingerprint: str) -> bool:
        """True only when this approval describes the book as it stands now."""
        return (
            self.status is ApprovalStatus.APPROVED
            and self.book_fingerprint == current_fingerprint
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reviewer": self.reviewer,
            "decided_at": self.decided_at,
            "book_fingerprint": self.book_fingerprint,
            "approved_artifacts": list(self.approved_artifacts),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApprovalRecord:
        try:
            return cls(
                status=ApprovalStatus(data.get("status", ApprovalStatus.PENDING.value)),
                reviewer=data.get("reviewer"),
                decided_at=data.get("decided_at"),
                book_fingerprint=data.get("book_fingerprint"),
                approved_artifacts=tuple(data.get("approved_artifacts", ())),
                notes=data.get("notes", ""),
            )
        except ValueError as exc:
            raise ModelError(f"approval record has an invalid status: {exc}") from None
