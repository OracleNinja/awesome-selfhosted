"""Runs the gates for a stage and aggregates the verdict.

The aggregation rule is the whole safety property, and it is one line: a stage
passes only when every gate returns PASS. FAIL and BLOCKED both stop the book,
and are reported distinctly so the fix is obvious — a FAIL means change the
book, a BLOCKED means fix the toolchain or supply the missing evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .base import GateResult, Status


@dataclass(frozen=True, slots=True)
class StageReport:
    """Every gate verdict for one stage, plus the aggregate."""

    stage: str
    results: tuple[GateResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.status.allows_advance for r in self.results)

    @property
    def failed(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status is Status.FAIL)

    @property
    def blocked(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status is Status.BLOCKED)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "gate_count": len(self.results),
            "failed": [r.gate_id for r in self.failed],
            "blocked": [r.gate_id for r in self.blocked],
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def summary(self) -> str:
        """A few lines a human can read in a terminal."""
        lines = [f"stage {self.stage}: {'PASS' if self.passed else 'STOP'}"]
        for result in self.results:
            mark = {
                Status.PASS: "ok  ",
                Status.FAIL: "FAIL",
                Status.BLOCKED: "BLKD",
            }[result.status]
            lines.append(f"  [{mark}] {result.gate_id}")
            for finding in result.findings:
                if finding.severity.value in ("blocker", "major"):
                    where = f" ({finding.subject})" if finding.subject else ""
                    lines.append(
                        f"         {finding.severity.value}: {finding.message}{where}"
                    )
        return "\n".join(lines)


def run_stage(stage: str, results: list[GateResult]) -> StageReport:
    """Aggregate already-computed gate results for a stage."""
    return StageReport(stage=stage, results=tuple(results))
