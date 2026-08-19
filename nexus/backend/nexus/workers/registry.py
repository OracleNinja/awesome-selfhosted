"""Handler registry: mapping a job's ``kind`` to the code that runs it.

Why a registry rather than importing a function by name from the payload:
importing a name that came from the database is arbitrary code execution. If an
attacker can write a row, they choose the module and function to import.
A registry means only kinds that were explicitly registered at import time can
ever run, and an unknown kind is an error rather than an opportunity.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.core.logging import get_logger
from nexus.db.models.job import Job

logger = get_logger(__name__)


@dataclass
class JobContext:
    """Everything a handler is given.

    Handlers receive a session that is already inside a transaction. They must
    not commit: the worker commits the handler's work and the job's completion
    together, so "the work happened" and "the job is done" cannot disagree.
    """

    job: Job
    session: AsyncSession
    settings: Settings

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload or {}

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested.

        Long handlers must check this between units of work. Cancellation is
        cooperative because the alternative — killing the task mid-transaction —
        leaves state half-applied with nothing recording that it happened.
        """
        return bool(self.job.cancel_requested)


class JobHandler(Protocol):
    """A handler is an async callable taking a context and returning a result.

    The returned dictionary is stored on the job, so an operator can see what
    the run actually did without reading logs.
    """

    async def __call__(self, context: JobContext) -> dict[str, Any] | None: ...


class HandlerRegistry:
    """Kind → handler, populated at import time."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, kind: str, *, description: str = "") -> Callable[[JobHandler], JobHandler]:
        """Decorator registering a handler for one job kind."""

        def decorator(handler: JobHandler) -> JobHandler:
            if kind in self._handlers:
                raise RuntimeError(f"Duplicate job handler for kind {kind!r}")
            self._handlers[kind] = handler
            self._descriptions[kind] = description or (handler.__doc__ or "").strip().split("\n")[0]
            return handler

        return decorator

    def resolve(self, kind: str) -> JobHandler | None:
        return self._handlers.get(kind)

    def known_kinds(self) -> dict[str, str]:
        """Registered kinds and their descriptions, for the jobs screen."""
        return dict(self._descriptions)

    def __len__(self) -> int:
        return len(self._handlers)


# The process-wide registry. Importing `nexus.workers.handlers` populates it;
# a handler module that is never imported is a kind that cannot run, which is
# why the handlers package imports every module explicitly.
registry = HandlerRegistry()

HandlerResult = Awaitable[dict[str, Any] | None]
