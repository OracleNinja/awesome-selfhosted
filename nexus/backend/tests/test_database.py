"""Database layer tests: schema integrity, transactions, and failure handling."""

from __future__ import annotations

import uuid
from datetime import UTC

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.errors import DependencyUnavailable
from nexus.db.models import AppSetting, AuditEvent, User, UserSession
from nexus.db.session import Database

pytestmark = pytest.mark.integration


def _user(**overrides) -> User:
    defaults = {
        "username": "ian",
        "display_name": "Ian",
        "password_hash": "$argon2id$placeholder",
        "role": "ADMIN",
    }
    return User(**{**defaults, **overrides})


class TestSchemaConstraints:
    async def test_usernames_are_unique(self, session: AsyncSession) -> None:
        session.add(_user())
        await session.flush()
        session.add(_user(display_name="Impostor"))
        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_role_check_constraint_rejects_unknown_roles(self, session: AsyncSession) -> None:
        """The database enforces the role vocabulary independently of Python,
        so a maintenance script writing raw SQL cannot invent a role."""
        session.add(_user(role="SUPERUSER"))
        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_short_usernames_are_rejected(self, session: AsyncSession) -> None:
        session.add(_user(username="ab"))
        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_deleting_a_user_cascades_to_sessions(self, session: AsyncSession) -> None:
        from datetime import datetime, timedelta

        user = _user()
        session.add(user)
        await session.flush()
        session.add(
            UserSession(
                user_id=user.id,
                token_hash="a" * 64,
                csrf_token_hash="b" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.flush()

        await session.delete(user)
        await session.flush()

        remaining = (await session.execute(select(UserSession))).scalars().all()
        assert remaining == []

    async def test_deleting_a_user_preserves_their_audit_history(
        self, session: AsyncSession
    ) -> None:
        """History must survive the deletion of its actor: an investigation
        months later still needs to know what happened, and the username is
        stored as a snapshot for exactly this case."""
        user = _user()
        session.add(user)
        await session.flush()
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                actor_username=user.username,
                actor_role=user.role,
                action="USER_LOGIN",
                outcome="SUCCESS",
                prev_hash="0" * 64,
                entry_hash="1" * 64,
            )
        )
        await session.flush()

        await session.delete(user)
        await session.flush()

        entry = (await session.execute(select(AuditEvent))).scalar_one()
        assert entry.actor_user_id is None
        assert entry.actor_username == "ian"

    async def test_audit_ids_cannot_be_chosen_by_the_caller(self, session: AsyncSession) -> None:
        """GENERATED ALWAYS means nobody can wedge a forged row between two
        existing links in the hash chain."""
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO audit_events "
                    "(id, actor_username, action, outcome, prev_hash, entry_hash) "
                    "VALUES (1, 'forged', 'X', 'SUCCESS', 'a', 'b')"
                )
            )

    async def test_audit_outcome_is_constrained(self, session: AsyncSession) -> None:
        session.add(
            AuditEvent(
                actor_username="ian",
                action="TEST",
                outcome="MAYBE",
                prev_hash="0" * 64,
                entry_hash="2" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_jsonb_settings_round_trip(self, session: AsyncSession) -> None:
        session.add(AppSetting(key="retention.events_days", value={"days": 30, "enabled": True}))
        await session.flush()
        session.expunge_all()

        stored = (await session.execute(select(AppSetting))).scalar_one()
        assert stored.value == {"days": 30, "enabled": True}

    async def test_timestamps_are_timezone_aware(self, session: AsyncSession) -> None:
        """Naive timestamps cannot be compared across a DST boundary, and an
        incident timeline that cannot be sorted is not a timeline."""
        user = _user()
        session.add(user)
        await session.flush()
        await session.refresh(user)
        assert user.created_at.tzinfo is not None


class TestTransactions:
    async def test_work_is_rolled_back_when_the_block_raises(self, database: Database) -> None:
        """The unit-of-work guarantee: an action and its audit entry are one
        transaction, so neither can be half-true."""

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            async with database.session() as session:
                session.add(_user(username="ghost"))
                await session.flush()
                raise Boom

        async with database.session() as session:
            found = (
                await session.execute(select(User).where(User.username == "ghost"))
            ).scalar_one_or_none()
        assert found is None

    async def test_work_is_committed_on_success(self, database: Database) -> None:
        async with database.session() as session:
            session.add(_user(username="persisted"))

        async with database.session() as session:
            found = (
                await session.execute(select(User).where(User.username == "persisted"))
            ).scalar_one_or_none()
        assert found is not None


class TestFailureHandling:
    async def test_health_check_never_raises(self) -> None:
        """A health probe that raises turns monitoring into a second outage."""
        from nexus.core.config import load_settings

        settings = load_settings(
            database_url="postgresql+asyncpg://nexus:nexus@127.0.0.1:1/nexus",
            db_connect_retries=0,
            db_pool_timeout_seconds=1,
        )
        db = Database(settings)
        await db.connect()
        health = await db.check_health()
        assert health.ok is False
        assert health.error
        await db.dispose()

    async def test_error_text_never_contains_the_password(self) -> None:
        from nexus.core.config import load_settings

        settings = load_settings(
            database_url="postgresql+asyncpg://nexus:hunter2@127.0.0.1:1/nexus",
            db_connect_retries=0,
            db_pool_timeout_seconds=1,
        )
        db = Database(settings)
        await db.connect()
        health = await db.check_health()
        assert "hunter2" not in (health.error or "")
        await db.dispose()

    async def test_session_on_uninitialised_database_raises_app_error(self) -> None:
        """Callers see DEPENDENCY_UNAVAILABLE, not a driver exception."""
        from nexus.core.config import load_settings

        db = Database(load_settings())
        with pytest.raises(DependencyUnavailable):
            async with db.session():
                pass  # pragma: no cover


class TestIdentifiers:
    async def test_entity_ids_are_uuids(self, session: AsyncSession) -> None:
        user = _user()
        session.add(user)
        await session.flush()
        assert isinstance(user.id, uuid.UUID)

    async def test_audit_ids_are_monotonic(self, session: AsyncSession) -> None:
        """The hash chain depends on a total order."""
        for index in range(3):
            session.add(
                AuditEvent(
                    actor_username="ian",
                    action="TEST",
                    outcome="SUCCESS",
                    prev_hash="0" * 64,
                    entry_hash=f"{index:064d}",
                )
            )
        await session.flush()
        ids = (await session.execute(select(AuditEvent.id).order_by(AuditEvent.id))).scalars().all()
        assert ids == sorted(ids)
        assert len(set(ids)) == 3
