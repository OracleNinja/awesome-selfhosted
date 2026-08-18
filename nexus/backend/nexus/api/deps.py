"""FastAPI dependencies.

Dependencies are how a route says what it needs — a database session, the
current user, a permission — without knowing how to build it. FastAPI resolves
them per request and, for generator dependencies, runs the teardown afterwards.

Everything here reads from :class:`~nexus.core.context.AppContext`, which the
application factory placed on ``app.state``. No module-level singletons: a test
builds its own context, and the same routes run against it unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.core.context import AppContext
from nexus.core.errors import DependencyUnavailable


def get_context(request: Request) -> AppContext:
    context: AppContext | None = getattr(request.app.state, "context", None)
    if context is None:  # pragma: no cover - would be a wiring bug
        raise DependencyUnavailable("Application context is not initialised.")
    return context


def get_settings(context: Annotated[AppContext, Depends(get_context)]) -> Settings:
    return context.settings


async def get_session(
    context: Annotated[AppContext, Depends(get_context)],
) -> AsyncIterator[AsyncSession]:
    """Provide a transactional session for the duration of the request.

    The session commits when the route returns normally and rolls back if it
    raises — including when a dependency further down the chain raises, and
    including the 500 path. That is what makes "an action and its audit entry
    are one transaction" true by construction rather than by convention.

    If the database is down, this raises ``DEPENDENCY_UNAVAILABLE`` (503)
    rather than a driver error, so the client sees the standard envelope and
    the frontend can show its degraded-mode banner instead of a stack trace.
    """
    if not context.database.available:
        # Re-probe rather than trusting the cached flag: the database may have
        # recovered since the last request, and refusing traffic to a healthy
        # database is its own outage.
        health = await context.database.check_health()
        if not health.ok:
            raise DependencyUnavailable(
                "The database is currently unavailable.",
                details={"component": "database"},
            )

    async with context.database.session() as session:
        yield session


ContextDep = Annotated[AppContext, Depends(get_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
