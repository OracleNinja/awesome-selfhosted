"""CLI tests: the bootstrap path, which is where default credentials creep in."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from nexus.cli import main
from nexus.core.config import Settings
from nexus.core.rbac import Role
from nexus.db.models.audit import AuditEvent
from nexus.db.models.user import User
from nexus.db.session import Database

pytestmark = pytest.mark.integration


async def run_cli(*args: str) -> int:
    """Invoke the CLI the way a shell would: in its own thread.

    The CLI owns its event loop (``asyncio.run``), which cannot be nested
    inside the one pytest-asyncio is already running. A worker thread has no
    running loop, so this exercises the real entry point unchanged rather than
    reaching past it into private helpers.
    """
    return await asyncio.to_thread(main, list(args))


@pytest.fixture(autouse=True)
def _cli_environment(
    settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the CLI at the test database.

    The CLI loads its own settings from the environment (it runs as a separate
    process in real life), so the test supplies them the same way an operator
    would.
    """
    monkeypatch.setenv("NEXUS_ENV_FILE", "/dev/null")
    monkeypatch.setenv("NEXUS_ENVIRONMENT", "testing")
    monkeypatch.setenv("NEXUS_DATABASE_URL", settings.database_url.get_secret_value())
    monkeypatch.setenv("NEXUS_DB_CONNECT_RETRIES", "0")
    monkeypatch.setenv("NEXUS_LOG_LEVEL", "CRITICAL")
    monkeypatch.setenv("NEXUS_PASSWORD_HASH_TIME_COST", "1")
    monkeypatch.setenv("NEXUS_PASSWORD_HASH_MEMORY_KIB", "8")
    monkeypatch.setenv("NEXUS_PASSWORD_HASH_PARALLELISM", "1")


class TestCreateAdmin:
    async def test_creates_an_admin_and_prints_the_password_once(
        self, database: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await run_cli("create-admin", "--username", "ian") == 0

        output = capsys.readouterr().out
        assert "Created administrator: ian" in output
        assert "shown once" in output

        async with database.session() as session:
            user = (await session.execute(select(User).where(User.username == "ian"))).scalar_one()
        assert user.role == Role.ADMIN.value
        assert user.is_active is True

    async def test_no_default_credentials_exist(self, database: Database) -> None:
        """Nothing creates an account until an operator with shell access asks
        for one. Default credentials are the most exploited weakness in
        self-hosted software."""
        async with database.session() as session:
            users = (await session.execute(select(User))).scalars().all()
        assert users == []

    async def test_generated_password_is_strong(self, capsys: pytest.CaptureFixture[str]) -> None:
        await run_cli("create-admin", "--username", "ian")
        printed = capsys.readouterr().out
        password_line = [line.strip() for line in printed.splitlines() if line.startswith("      ")]
        assert password_line
        assert len(password_line[0]) >= 20

    async def test_password_is_hashed_not_stored(
        self, database: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await run_cli("create-admin", "--username", "ian")
        printed = capsys.readouterr().out
        password = next(line.strip() for line in printed.splitlines() if line.startswith("      "))

        async with database.session() as session:
            user = (await session.execute(select(User).where(User.username == "ian"))).scalar_one()
        assert user.password_hash.startswith("$argon2id$")
        assert password not in user.password_hash

    async def test_refuses_a_second_admin_without_force(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await run_cli("create-admin", "--username", "ian") == 0
        capsys.readouterr()
        assert await run_cli("create-admin", "--username", "second") == 1
        assert "already has" in capsys.readouterr().err

    async def test_force_allows_a_second_admin(self) -> None:
        assert await run_cli("create-admin", "--username", "ian") == 0
        assert await run_cli("create-admin", "--username", "second", "--force") == 0

    async def test_bootstrap_is_audited(self, database: Database) -> None:
        await run_cli("create-admin", "--username", "ian")
        async with database.session() as session:
            actions = (await session.execute(select(AuditEvent.action))).scalars().all()
        assert "USER_CREATED" in actions
        assert "ADMIN_BOOTSTRAPPED" in actions


class TestVerifyAudit:
    async def test_reports_a_healthy_chain(self, capsys: pytest.CaptureFixture[str]) -> None:
        await run_cli("create-admin", "--username", "ian")
        capsys.readouterr()

        assert await run_cli("verify-audit") == 0
        output = capsys.readouterr().out
        assert "Audit chain OK" in output
        # Honest about what a pass does and does not prove.
        assert "NOT CONFIGURED" in output
        assert "internal" in output

    async def test_detects_tampering_and_exits_non_zero(
        self, database: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from sqlalchemy import text

        await run_cli("create-admin", "--username", "ian")
        capsys.readouterr()

        async with database.session() as session:
            await session.execute(text("UPDATE audit_events SET actor_username = 'someone_else'"))

        assert await run_cli("verify-audit") == 2
        assert "FAILED" in capsys.readouterr().err


class TestShowConfig:
    async def test_prints_redacted_configuration(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "NEXUS_DATABASE_URL", "postgresql+asyncpg://nexus:hunter2@localhost/nexus"
        )
        assert await run_cli("show-config") == 0
        output = capsys.readouterr().out
        assert "hunter2" not in output
        assert "environment" in output

    async def test_warns_when_no_networks_are_declared(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty means every active operation is refused; the operator should
        not have to discover that by trying one."""
        await run_cli("show-config")
        assert "WARNING" in capsys.readouterr().out
