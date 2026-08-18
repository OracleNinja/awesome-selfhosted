"""Alembic environment.

Migrations are how a schema changes in a system you cannot take offline and
cannot edit by hand. Each migration is a reviewable, ordered, reversible unit;
the ``alembic_version`` table records which one the database is at. That makes
"what schema is production running?" a query rather than an archaeology
project, and it makes a rollback a command rather than an improvisation.

Two details worth understanding:

* **Async engine.** The application uses asyncpg, so the URL carries the
  ``+asyncpg`` driver. Alembic's API is synchronous, so we open an async
  connection and hand it to ``connection.run_sync``, which runs the
  synchronous migration code inside the async connection's context.
* **Transactional DDL.** PostgreSQL can roll back schema changes. Each
  migration runs in one transaction: it applies completely or not at all,
  leaving no half-migrated database to reason about at 3am.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from nexus.core.config import load_settings
from nexus.db.base import Base

# Importing the models package is what populates Base.metadata; without it,
# autogenerate would see an empty schema and propose dropping every table.
import nexus.db.models  # noqa: F401  (side-effecting import, intentional)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the URL from validated settings, not from alembic.ini.

    A URL set with ``-x db_url=...`` wins, which is how the test harness points
    migrations at a throwaway database without touching the environment.
    """
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    return load_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    ``alembic upgrade head --sql`` produces a script a DBA can review and apply
    through a change-control process — the normal path in environments where
    the application is not permitted to run DDL itself.
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, autogenerate misses column type and default changes,
        # producing migrations that silently do not match the models.
        compare_type=True,
        compare_server_default=True,
        # Constraint names come from the metadata naming convention so that a
        # downgrade can drop exactly what the upgrade created.
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        # NullPool: a migration run is a short-lived process. Pooling would
        # keep connections open after the work is done and delay exit.
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
