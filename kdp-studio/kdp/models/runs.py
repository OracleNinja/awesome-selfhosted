"""Execution records.

One entry per stage attempt: who ran, against what, with what result. The
pipeline is resumable because this says where it got to, and auditable because
it says how.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ModelError


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One stage execution."""

    run_id: str
    book_id: str
    stage: str
    #: Which agent or process ran it.
    agent: str = ""
    #: Gate outcome for the stage: pass | fail | blocked.
    status: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    #: Artefacts consumed and produced, by path or asset id.
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    #: Gate ids evaluated during this run.
    gates: tuple[str, ...] = field(default_factory=tuple)
    #: How many times this stage had been attempted before, for retry policy.
    attempt: int = 1
    error: str | None = None
    #: Package version that produced the record.
    version: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ModelError("run_id must not be empty")

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "book_id": self.book_id,
            "stage": self.stage,
            "agent": self.agent,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "gates": list(self.gates),
            "attempt": self.attempt,
            "error": self.error,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RunRecord:
        try:
            return cls(
                run_id=data["run_id"],
                book_id=data.get("book_id", ""),
                stage=data.get("stage", ""),
                agent=data.get("agent", ""),
                status=data.get("status", ""),
                started_at=data.get("started_at"),
                finished_at=data.get("finished_at"),
                inputs=tuple(data.get("inputs", ())),
                outputs=tuple(data.get("outputs", ())),
                gates=tuple(data.get("gates", ())),
                attempt=data.get("attempt", 1),
                error=data.get("error"),
                version=data.get("version", ""),
            )
        except KeyError as exc:
            raise ModelError(f"run record missing field {exc.args[0]!r}") from None
