"""Gate primitives.

A gate is a deterministic function from evidence to a verdict. No gate calls a
model, and no gate consults the network or the clock: given the same inputs it
returns the same result, forever. That is what makes a stored gate result worth
keeping.

**Three statuses, and only one of them lets a book through.**

``PASS``     the gate was evaluated and the book satisfies it.
``FAIL``     the gate was evaluated and the book does not satisfy it.
``BLOCKED``  the gate could not be evaluated — a dependency is missing, or the
             evidence it needs was not supplied.

``BLOCKED`` is the important one. The tempting implementation treats "I could
not check" as "nothing found" and lets the book past; that turns an uninstalled
library into a silent quality regression, and the failure is invisible exactly
when it matters. Here a gate that cannot run stops the book, and says what it
would have needed to run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"

    @property
    def allows_advance(self) -> bool:
        return self is Status.PASS


class Severity(str, Enum):
    #: Stops the book. Any blocker finding forces a FAIL.
    BLOCKER = "blocker"
    #: Should be fixed, does not stop the book on its own.
    MAJOR = "major"
    #: Cosmetic or advisory.
    MINOR = "minor"
    #: Recorded for the audit trail; carries no judgement.
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Finding:
    """One specific thing a gate observed."""

    code: str
    severity: Severity
    message: str
    #: Where it was observed: an asset_id, a page_id, a field name.
    subject: str | None = None
    #: Machine-readable supporting values.
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "subject": self.subject,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GateResult:
    """The verdict of one gate, in a form that can be stored and re-read."""

    gate_id: str
    stage: str
    status: Status
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    #: Values the gate computed while deciding, kept for the audit trail.
    evidence: dict = field(default_factory=dict)
    spec_revision: str = ""

    @property
    def blockers(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.BLOCKER)

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "stage": self.stage,
            "status": self.status.value,
            "findings": [f.to_dict() for f in self.findings],
            "evidence": self.evidence,
            "spec_revision": self.spec_revision,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"


def decide(
    gate_id: str,
    stage: str,
    findings: list[Finding],
    evidence: dict | None = None,
    blocked_reason: str | None = None,
) -> GateResult:
    """Build a result, deriving the status from the findings.

    ``blocked_reason`` short-circuits to ``BLOCKED``; otherwise any blocker
    finding produces ``FAIL`` and anything else produces ``PASS``.
    """
    from ..specs import SPEC_REVISION

    findings = list(findings)
    if blocked_reason is not None:
        findings.insert(
            0,
            Finding(
                code="gate.blocked",
                severity=Severity.BLOCKER,
                message=blocked_reason,
            ),
        )
        status = Status.BLOCKED
    elif any(f.severity is Severity.BLOCKER for f in findings):
        status = Status.FAIL
    else:
        status = Status.PASS

    return GateResult(
        gate_id=gate_id,
        stage=stage,
        status=status,
        findings=tuple(findings),
        evidence=evidence or {},
        spec_revision=SPEC_REVISION,
    )
