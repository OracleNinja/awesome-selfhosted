"""The stage machine.

Six stages, in order, each independently testable and each separated from the
next by a gate. The rules are deliberately rigid:

* Stages advance one at a time, in order. No skipping.
* A stage advances only on a passing :class:`StageReport` for the *current*
  stage — passing some other stage's gates does not count.
* Advancement is a pure function returning a new manifest. Nothing mutates,
  so a rejected advance leaves no trace on the book.

The rigidity is the feature. An orchestrator that can be talked into skipping
QA is not a quality system.
"""

from __future__ import annotations

from typing import Final

from .errors import PipelineError
from .gates.runner import StageReport
from .models.manifest import BookManifest

#: The stage order. Index in this tuple is the stage's ordinal.
#:
#: The sixteen conceptual steps in the build brief map onto these eleven
#: working stages plus the terminal one. Several conceptual steps share a
#: stage because they share an artefact and a gate — splitting them would add
#: transitions without adding a decision. The mapping is documented in
#: ARCHITECTURE.md and asserted in the tests, so it cannot drift silently.
STAGES: Final[tuple[str, ...]] = (
    "research",             # market research
    "strategy",             # opportunity and differentiation
    "concept",              # book specification + page architecture
    "planning",             # prompt and asset plan
    "generation",           # asset generation, with attempts
    "qa",                   # asset QA, duplication, repair loop
    "production",           # interior assembly + cover production
    "production_qa",        # interior QA + cover QA
    "publishing_prep",      # metadata/listing + KDP compliance
    "final_audit",          # final publishing audit
    "human_approval",       # explicit human sign-off
    "ready_for_submission", # publication package built; a human submits
)

#: The terminal stage. Reaching it means every gate passed; it does not mean
#: anything has been published, and nothing in this package publishes.
TERMINAL_STAGE: Final[str] = "ready_for_submission"


def stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        raise PipelineError(
            f"unknown stage {stage!r}; stages are {', '.join(STAGES)}"
        ) from None


def next_stage(stage: str) -> str:
    index = stage_index(stage)
    if stage == TERMINAL_STAGE:
        raise PipelineError(f"{TERMINAL_STAGE!r} is terminal; there is no next stage")
    return STAGES[index + 1]


def can_advance(manifest: BookManifest, report: StageReport) -> tuple[bool, str]:
    """Whether ``manifest`` may move on, and why not when it may not."""
    if manifest.stage == TERMINAL_STAGE:
        return False, f"book is already at the terminal stage {TERMINAL_STAGE!r}"
    if report.stage != manifest.stage:
        return False, (
            f"report is for stage {report.stage!r} but the book is at "
            f"{manifest.stage!r}; a book cannot be advanced on another stage's "
            "gates"
        )
    if not report.results:
        return False, f"no gate results supplied for stage {report.stage!r}"
    if not report.passed:
        blocked = [r.gate_id for r in report.blocked]
        failed = [r.gate_id for r in report.failed]
        parts = []
        if failed:
            parts.append(f"failed: {', '.join(failed)}")
        if blocked:
            parts.append(f"blocked: {', '.join(blocked)}")
        return False, "; ".join(parts)
    return True, ""


def advance(manifest: BookManifest, report: StageReport) -> BookManifest:
    """Return the manifest at the next stage, or raise.

    Raising rather than returning a flag is deliberate: an orchestrator that
    ignores a return value should not be able to advance a book by accident.
    """
    allowed, reason = can_advance(manifest, report)
    if not allowed:
        raise PipelineError(
            f"cannot advance {manifest.book_id!r} from {manifest.stage!r}: {reason}"
        )
    return manifest.with_stage(next_stage(manifest.stage))
