"""Audit service tests: the chain, and the guarantees around it."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.config import Settings
from nexus.db.models.audit import GENESIS_HASH, AuditEvent
from nexus.db.session import Database
from nexus.services.audit import (
    AuditAction,
    AuditActor,
    AuditOutcome,
    AuditService,
    compute_entry_hash,
)

pytestmark = pytest.mark.integration


def _service(session: AsyncSession, settings: Settings) -> AuditService:
    return AuditService(session, settings)


class TestAppending:
    async def test_first_entry_links_to_genesis(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        entry = await _service(session, settings).record(
            AuditAction.SYSTEM_STARTED, actor=AuditActor.system()
        )
        assert entry.prev_hash == GENESIS_HASH
        assert len(entry.entry_hash) == 64

    async def test_each_entry_links_to_the_previous(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        audit = _service(session, settings)
        first = await audit.record(AuditAction.SYSTEM_STARTED, actor=AuditActor.system())
        second = await audit.record(AuditAction.LOGOUT, actor=AuditActor.system())
        assert second.prev_hash == first.entry_hash

    async def test_records_the_essentials(self, session: AsyncSession, settings: Settings) -> None:
        entry = await _service(session, settings).record(
            AuditAction.USER_CREATED,
            actor=AuditActor(username="ian", role="ADMIN"),
            target_type="user",
            target_id="abc",
            target_label="newuser",
            reason="Onboarding",
            source_ip="192.168.1.10",
            details={"role": "VIEWER"},
        )
        assert entry.actor_username == "ian"
        assert entry.action == "USER_CREATED"
        assert entry.outcome == "SUCCESS"
        assert entry.reason == "Onboarding"
        assert entry.details == {"role": "VIEWER"}

    async def test_outcome_can_be_failure_or_denied(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        audit = _service(session, settings)
        failed = await audit.record(
            AuditAction.LOGIN_FAILED,
            actor=AuditActor.anonymous("nobody"),
            outcome=AuditOutcome.FAILURE,
        )
        denied = await audit.record(
            AuditAction.PERMISSION_DENIED,
            actor=AuditActor(username="viewer"),
            outcome=AuditOutcome.DENIED,
        )
        assert failed.outcome == "FAILURE"
        assert denied.outcome == "DENIED"


class TestDetailSanitisation:
    async def test_credentials_are_stripped_from_details(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """The audit log is exported and read casually; a credential landing
        here would spread furthest."""
        entry = await _service(session, settings).record(
            AuditAction.USER_CREATED,
            actor=AuditActor.system(),
            details={"password": "hunter2", "api_key": "abc", "role": "ADMIN"},
        )
        assert entry.details["password"] == "[redacted]"
        assert entry.details["api_key"] == "[redacted]"
        assert entry.details["role"] == "ADMIN"

    async def test_oversized_details_are_truncated(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        entry = await _service(session, settings).record(
            AuditAction.SETTING_CHANGED,
            actor=AuditActor.system(),
            details={"blob": "x" * 32_000},
        )
        assert entry.details["truncated"] is True
        assert "blob" in entry.details["keys"]


class TestVerification:
    async def test_empty_log_verifies(self, session: AsyncSession, settings: Settings) -> None:
        result = await _service(session, settings).verify_chain()
        assert result.ok is True
        assert result.entries_checked == 0

    async def test_untouched_chain_verifies(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        audit = _service(session, settings)
        for index in range(5):
            await audit.record(
                AuditAction.SYSTEM_STARTED,
                actor=AuditActor.system(),
                details={"n": index},
            )
        result = await audit.verify_chain()
        assert result.ok is True
        assert result.entries_checked == 5

    async def test_edited_entry_is_detected(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """The point of the whole mechanism: altering history is visible."""
        audit = _service(session, settings)
        for index in range(4):
            await audit.record(
                AuditAction.USER_CREATED,
                actor=AuditActor.system(),
                details={"n": index},
            )
        await session.flush()

        # Tamper the way an attacker would: change the record, leave the hashes.
        target = (
            await session.execute(select(AuditEvent).order_by(AuditEvent.id).limit(1))
        ).scalar_one()
        await session.execute(
            text("UPDATE audit_events SET reason = 'covering my tracks' WHERE id = :id"),
            {"id": target.id},
        )
        session.expunge_all()

        result = await audit.verify_chain()
        assert result.ok is False
        assert result.first_invalid_id == target.id
        assert "hash" in (result.detail or "").lower()

    async def test_deleted_entry_is_detected(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Removing a middle entry breaks the link in the one after it."""
        audit = _service(session, settings)
        for index in range(4):
            await audit.record(
                AuditAction.USER_CREATED, actor=AuditActor.system(), details={"n": index}
            )
        await session.flush()

        ids = (await session.execute(select(AuditEvent.id).order_by(AuditEvent.id))).scalars().all()
        await session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": ids[1]})
        session.expunge_all()

        result = await audit.verify_chain()
        assert result.ok is False
        assert result.first_invalid_id == ids[2]

    async def test_pruning_the_oldest_end_still_verifies(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Retention legitimately deletes the oldest entries. A verifier that
        insisted on reaching the genesis row would cry tampering every time the
        retention job ran."""
        audit = _service(session, settings)
        for index in range(5):
            await audit.record(
                AuditAction.SYSTEM_STARTED, actor=AuditActor.system(), details={"n": index}
            )
        await session.flush()

        ids = (await session.execute(select(AuditEvent.id).order_by(AuditEvent.id))).scalars().all()
        await session.execute(
            text("DELETE FROM audit_events WHERE id IN (:a, :b)"),
            {"a": ids[0], "b": ids[1]},
        )
        session.expunge_all()

        result = await audit.verify_chain()
        assert result.ok is True
        assert result.entries_checked == 3


class TestHashFunction:
    def test_hash_is_deterministic_regardless_of_key_order(self) -> None:
        """Canonicalisation is what stops the verifier reporting false
        tampering on an untouched row."""
        left = compute_entry_hash({"a": 1, "b": 2}, "0" * 64)
        right = compute_entry_hash({"b": 2, "a": 1}, "0" * 64)
        assert left == right

    def test_changing_content_changes_the_hash(self) -> None:
        base = compute_entry_hash({"action": "LOGIN"}, "0" * 64)
        changed = compute_entry_hash({"action": "LOGOUT"}, "0" * 64)
        assert base != changed

    def test_changing_the_predecessor_changes_the_hash(self) -> None:
        base = compute_entry_hash({"action": "LOGIN"}, "0" * 64)
        rechained = compute_entry_hash({"action": "LOGIN"}, "1" * 64)
        assert base != rechained


class TestTransactionSemantics:
    async def test_audit_row_rolls_back_with_its_action(
        self, database: Database, settings: Settings
    ) -> None:
        """The core guarantee: the log never claims something happened that
        did not."""

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            async with database.session() as db_session:
                await _service(db_session, settings).record(
                    AuditAction.USER_DEACTIVATED, actor=AuditActor.system()
                )
                raise Boom

        async with database.session() as db_session:
            count = len((await db_session.execute(select(AuditEvent))).scalars().all())
        assert count == 0


class TestConcurrency:
    async def test_concurrent_appends_do_not_fork_the_chain(
        self, database: Database, settings: Settings
    ) -> None:
        """Without the advisory lock, two transactions read the same prev_hash
        and produce two entries claiming the same predecessor."""

        async def append(index: int) -> None:
            async with database.session() as db_session:
                await _service(db_session, settings).record(
                    AuditAction.SYSTEM_STARTED,
                    actor=AuditActor.system(),
                    details={"worker": index},
                )

        await asyncio.gather(*(append(index) for index in range(8)))

        async with database.session() as db_session:
            result = await _service(db_session, settings).verify_chain()
            entries = (
                (await db_session.execute(select(AuditEvent).order_by(AuditEvent.id)))
                .scalars()
                .all()
            )

        assert result.ok is True
        assert result.entries_checked == 8
        # Every predecessor is claimed exactly once.
        assert len({entry.prev_hash for entry in entries}) == 8
