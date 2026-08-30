"""Stage separation.

The pipeline's value is entirely in what it refuses. These tests are mostly
about refusal.
"""

from __future__ import annotations

import pytest

from kdp.errors import PipelineError
from kdp.gates.base import GateResult, Status
from kdp.gates.runner import run_stage
from kdp.pipeline import STAGES, TERMINAL_STAGE, advance, can_advance, next_stage


def result(gate_id: str, stage: str, status: Status) -> GateResult:
    return GateResult(gate_id=gate_id, stage=stage, status=status)


def passing(stage: str):
    return run_stage(stage, [result("g", stage, Status.PASS)])


def test_stage_order_is_fixed_and_terminates():
    assert STAGES[0] == "research"
    assert STAGES[-1] == TERMINAL_STAGE
    assert len(set(STAGES)) == len(STAGES)


def test_next_stage_walks_the_whole_pipeline():
    stage = STAGES[0]
    for expected in STAGES[1:]:
        stage = next_stage(stage)
        assert stage == expected


def test_terminal_stage_has_no_successor():
    with pytest.raises(PipelineError, match="terminal"):
        next_stage(TERMINAL_STAGE)


def test_unknown_stage_is_rejected():
    with pytest.raises(PipelineError, match="unknown stage"):
        next_stage("marketing")


def test_passing_gates_advance_one_stage(manifest):
    at_research = manifest.with_stage("research")
    assert advance(at_research, passing("research")).stage == "concept"


def test_failed_gate_stops_the_book(manifest):
    report = run_stage("qa", [result("G4", "qa", Status.FAIL)])
    allowed, reason = can_advance(manifest, report)
    assert allowed is False
    assert "failed: G4" in reason
    with pytest.raises(PipelineError):
        advance(manifest, report)


def test_blocked_gate_stops_the_book_just_like_a_failure(manifest):
    # The property that matters: a gate that could not run does not let a
    # book through.
    report = run_stage("qa", [result("G4", "qa", Status.BLOCKED)])
    allowed, reason = can_advance(manifest, report)
    assert allowed is False
    assert "blocked: G4" in reason


def test_one_failing_gate_among_passing_ones_stops_the_book(manifest):
    report = run_stage(
        "qa",
        [result("a", "qa", Status.PASS), result("b", "qa", Status.FAIL)],
    )
    assert can_advance(manifest, report)[0] is False


def test_another_stage_s_gates_cannot_advance_this_one(manifest):
    # Blocks the obvious way to launder a book past QA.
    allowed, reason = can_advance(manifest, passing("research"))
    assert allowed is False
    assert "another stage's gates" in reason


def test_empty_report_never_advances(manifest):
    allowed, reason = can_advance(manifest, run_stage("qa", []))
    assert allowed is False
    assert "no gate results" in reason


def test_terminal_book_does_not_advance_again(manifest):
    done = manifest.with_stage(TERMINAL_STAGE)
    allowed, _ = can_advance(done, passing(TERMINAL_STAGE))
    assert allowed is False


def test_advance_does_not_mutate_the_original(manifest):
    at_research = manifest.with_stage("research")
    advance(at_research, passing("research"))
    assert at_research.stage == "research"


def test_stages_cannot_be_skipped(manifest):
    # There is no API that moves a book more than one stage at a time.
    at_research = manifest.with_stage("research")
    assert advance(at_research, passing("research")).stage == "concept"


def test_report_summary_names_failing_gates(manifest):
    report = run_stage("qa", [result("G4.originality", "qa", Status.FAIL)])
    summary = report.summary()
    assert "STOP" in summary
    assert "G4.originality" in summary
