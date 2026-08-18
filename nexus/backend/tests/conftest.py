"""Shared test fixtures.

Testing philosophy for this project
-----------------------------------
Tests run against a **real PostgreSQL database**, not SQLite and not mocks.
The schema uses JSONB, INET, partial indexes, ``ON DELETE`` behaviour, and
``GENERATED ALWAYS AS IDENTITY`` — none of which SQLite implements. A test
suite that passes against a different database than production runs is a test
suite that tells you nothing about production.

The schema is created by running the real migrations, so every test also
exercises the migration path. If a migration is missing, every test fails,
which is a much louder signal than discovering it at deploy time.

Isolation between tests is by truncation rather than by recreating the schema:
truncating four tables takes microseconds, whereas re-running migrations for
each test would put the suite into minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.app import create_app
from nexus.core.config import Environment, LogFormat, Settings, load_settings
from nexus.core.context import AppContext
from nexus.db.session import Database

BACKEND_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://nexus:nexus@127.0.0.1:5432/nexus_test"


def test_database_url() -> str:
    """Where the suite writes.

    Overridable so CI can point at its service container. The name must differ
    from the development database: these fixtures TRUNCATE tables, and pointing
    them at a database with real data would destroy it.
    """
    return os.environ.get("NEXUS_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings for the suite.

    Built explicitly rather than from the environment so a developer's local
    ``.env`` cannot change test behaviour — a test that passes on one machine
    and fails on another because of an environment variable is worse than no
    test.
    """
    return load_settings(
        environment=Environment.TESTING,
        database_url=test_database_url(),
        log_format=LogFormat.CONSOLE,
        log_level="WARNING",
        secret_key="test-secret-key-not-used-outside-the-test-suite",
        session_cookie_secure=False,
        monitored_networks=["192.168.1.0/24", "10.10.0.0/16"],
        data_dir=BACKEND_ROOT / "var" / "test",
        # Fail fast: a test run should not spend 30 seconds retrying a database
        # that is not there. The error message tells the developer what to start.
        db_connect_retries=0,
    )


@pytest.fixture(scope="session")
def _migrated_schema(settings: Settings) -> Iterator[None]:
    """Create the schema once per session by running the real migrations.

    Synchronous on purpose: Alembic's async env calls ``asyncio.run``, which
    cannot be nested inside a running event loop. A sync fixture has no loop
    running, so this works and keeps Alembic's normal code path under test.
    """
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    url = settings.database_url.get_secret_value()
    config.cmd_opts = None
    config.attributes["configure_logger"] = False
    # -x db_url is how env.py accepts an override without touching os.environ.
    config.cmd_opts = type("Opts", (), {"x": [f"db_url={url}"]})()

    # A brand-new database has nothing to downgrade; that is not an error.
    with contextlib.suppress(Exception):
        command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    # The schema is left in place after the run so a failing test can be
    # investigated with psql. The next run downgrades first.


@pytest.fixture
async def database(settings: Settings, _migrated_schema: None) -> AsyncIterator[Database]:
    """A connected :class:`Database`, truncated before the test body runs."""
    db = Database(settings)
    connected = await db.connect()
    if not connected:
        pytest.fail(
            "PostgreSQL is not reachable at "
            f"{test_database_url().split('@')[-1]}. Start it, or set "
            "NEXUS_TEST_DATABASE_URL."
        )
    await _truncate_all(db)
    try:
        yield db
    finally:
        await db.dispose()


async def _truncate_all(db: Database) -> None:
    """Empty every table, resetting identity sequences.

    ``RESTART IDENTITY`` matters for the audit log: its hash chain and its
    ordering both key off the identity column, and a test that expects the
    first entry to be id 1 should not depend on how many tests ran before it.
    ``CASCADE`` handles the foreign keys.
    """
    async with db.engine.begin() as connection:
        tables = (
            (
                await connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            await connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """A session bound to a transaction that is always rolled back.

    The session never commits, so a test that deliberately triggers an
    ``IntegrityError`` — proving a constraint works — does not leave the
    connection in a state that breaks teardown. It also means these tests
    cannot leak rows into another test regardless of what they do.
    """
    async with database.engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
        db_session = maker()
        try:
            yield db_session
        finally:
            await db_session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest.fixture
async def context(settings: Settings, database: Database) -> AsyncIterator[AppContext]:
    """An :class:`AppContext` sharing the test database.

    The database is injected rather than created by the context so the fixture
    above stays the single owner of connection lifecycle and truncation.
    """
    ctx = AppContext(settings, database=database)
    await ctx.startup()
    try:
        yield ctx
    finally:
        # Only the context's own state is torn down here; disposing the shared
        # Database is the `database` fixture's job.
        ctx._ready = False


@pytest.fixture
async def client(settings: Settings, context: AppContext) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the app in-process.

    ``ASGITransport`` calls the application directly — no socket, no port, no
    race between "server started" and "test ran". The request path is otherwise
    identical: middleware, dependencies, and exception handlers all run.
    """
    app = create_app(settings)
    app.state.context = context  # reuse the migrated, truncated database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def anyio_backend() -> str:  # pragma: no cover - required by httpx internals
    return "asyncio"


def pytest_configure(config: pytest.Config) -> None:
    """Fail loudly and early if the suite is pointed at a non-test database."""
    url = test_database_url()
    if "test" not in url.rsplit("/", 1)[-1]:
        raise pytest.UsageError(
            f"Refusing to run: NEXUS_TEST_DATABASE_URL ({url}) does not look "
            "like a test database. These fixtures TRUNCATE every table."
        )
    # Keep asyncio's own debug noise out of test output unless requested.
    asyncio.get_event_loop_policy()
