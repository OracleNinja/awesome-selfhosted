"""User administration tests, with emphasis on the guards.

The CRUD is unremarkable. What matters is that an administrator cannot make the
system unadministrable, that a role change takes effect immediately rather than
whenever a session happens to expire, and that every change is recorded.
"""

from __future__ import annotations

import pytest
from conftest import TEST_PASSWORD, LoggedInClient, create_test_user
from httpx import AsyncClient
from sqlalchemy import select

from nexus.core.config import Settings
from nexus.core.rbac import Role
from nexus.db.models.audit import AuditEvent
from nexus.db.models.user import User
from nexus.db.session import Database

pytestmark = pytest.mark.integration

USERS = "/api/v1/users"
REASON = {"reason": "Testing the guard rails."}


async def _create(admin: LoggedInClient, **overrides) -> dict:
    payload = {
        "username": "newperson",
        "display_name": "New Person",
        "role": "VIEWER",
        **overrides,
    }
    response = await admin.client.post(USERS, headers=admin.headers(), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestCreate:
    async def test_creates_a_user_with_a_generated_password(self, admin: LoggedInClient) -> None:
        body = await _create(admin)
        assert body["user"]["username"] == "newperson"
        assert body["user"]["role"] == "VIEWER"
        # Returned once, so the admin can hand it over; never stored in the clear.
        assert body["generated_password"]
        assert len(body["generated_password"]) >= 16

    async def test_generated_password_actually_works(
        self, admin: LoggedInClient, second_client: AsyncClient
    ) -> None:
        body = await _create(admin)
        login = await second_client.post(
            "/api/v1/auth/login",
            json={
                "username": "newperson",
                "password": body["generated_password"],
            },
        )
        assert login.status_code == 200

    async def test_password_hash_is_never_returned(self, admin: LoggedInClient) -> None:
        body = await _create(admin)
        assert "password_hash" not in str(body)

    async def test_usernames_are_case_folded(self, admin: LoggedInClient) -> None:
        body = await _create(admin, username="MixedCase")
        assert body["user"]["username"] == "mixedcase"

    async def test_duplicate_username_is_a_conflict(self, admin: LoggedInClient) -> None:
        await _create(admin)
        response = await admin.client.post(
            USERS,
            headers=admin.headers(),
            json={"username": "newperson", "display_name": "Clash", "role": "VIEWER"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_case_variant_username_is_also_a_conflict(self, admin: LoggedInClient) -> None:
        """Otherwise "Ian" and "ian" become two accounts that look like one."""
        await _create(admin, username="ian")
        response = await admin.client.post(
            USERS,
            headers=admin.headers(),
            json={"username": "IAN", "display_name": "Impostor", "role": "ADMIN"},
        )
        assert response.status_code == 409

    async def test_invalid_username_is_rejected(self, admin: LoggedInClient) -> None:
        response = await admin.client.post(
            USERS,
            headers=admin.headers(),
            json={"username": "bad user!", "display_name": "X", "role": "VIEWER"},
        )
        assert response.status_code == 422

    async def test_weak_supplied_password_is_rejected(self, admin: LoggedInClient) -> None:
        response = await admin.client.post(
            USERS,
            headers=admin.headers(),
            json={
                "username": "weakling",
                "display_name": "X",
                "role": "VIEWER",
                "password": "short",
            },
        )
        assert response.status_code == 422

    async def test_creation_is_audited_without_the_password(
        self, admin: LoggedInClient, database: Database
    ) -> None:
        await _create(admin)
        async with database.session() as db_session:
            entry = (
                await db_session.execute(
                    select(AuditEvent).where(AuditEvent.action == "USER_CREATED")
                )
            ).scalar_one()
        assert entry.actor_username == "admin_user"
        assert entry.target_label == "newperson"
        assert entry.details["credential_generated"] is True
        assert entry.details["force_credential_change"] is True

    async def test_generated_password_never_reaches_the_audit_log(
        self, admin: LoggedInClient, database: Database
    ) -> None:
        body = await _create(admin)
        secret = body["generated_password"]
        async with database.session() as db_session:
            entries = (await db_session.execute(select(AuditEvent))).scalars().all()
        assert entries
        for entry in entries:
            assert secret not in str(entry.details)
            assert secret not in (entry.reason or "")


class TestRoleChanges:
    async def test_admin_can_promote_a_viewer(self, admin: LoggedInClient) -> None:
        created = await _create(admin)
        response = await admin.client.put(
            f"{USERS}/{created['user']['id']}/role",
            headers=admin.headers(),
            json={"role": "OPERATOR", **REASON},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "OPERATOR"

    async def test_reason_is_required(self, admin: LoggedInClient) -> None:
        created = await _create(admin)
        response = await admin.client.put(
            f"{USERS}/{created['user']['id']}/role",
            headers=admin.headers(),
            json={"role": "OPERATOR"},
        )
        assert response.status_code == 422

    async def test_role_change_takes_effect_immediately(
        self,
        admin: LoggedInClient,
        second_client: AsyncClient,
        database: Database,
        settings: Settings,
    ) -> None:
        """A demoted operator must not keep their old permissions until their
        session expires — that window is exactly what an insider would use."""
        await create_test_user(database, settings, username="demoteme", role=Role.ADMIN)
        login = await second_client.post(
            "/api/v1/auth/login",
            json={"username": "demoteme", "password": TEST_PASSWORD},
        )
        assert login.status_code == 200
        assert (await second_client.get(USERS)).status_code == 200

        async with database.session() as db_session:
            target = (
                await db_session.execute(select(User).where(User.username == "demoteme"))
            ).scalar_one()
            target_id = target.id

        response = await admin.client.put(
            f"{USERS}/{target_id}/role",
            headers=admin.headers(),
            json={"role": "VIEWER", **REASON},
        )
        assert response.status_code == 200

        # Their session was revoked, so they are signed out, not merely demoted.
        assert (await second_client.get(USERS)).status_code == 401

    async def test_cannot_demote_yourself(self, admin: LoggedInClient) -> None:
        """An admin who does this by accident cannot undo it."""
        response = await admin.client.put(
            f"{USERS}/{admin.user_id}/role",
            headers=admin.headers(),
            json={"role": "VIEWER", **REASON},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "OPERATION_NOT_PERMITTED"

    async def test_cannot_remove_the_last_admin(
        self, admin: LoggedInClient, database: Database, settings: Settings
    ) -> None:
        """Leaving a deployment with no administrator means the only remedy is
        editing the database by hand."""
        await create_test_user(database, settings, username="second", role=Role.ADMIN)
        async with database.session() as db_session:
            second = (
                await db_session.execute(select(User).where(User.username == "second"))
            ).scalar_one()
            second_id = second.id

        # Demoting the *other* admin is fine while `admin` still holds the role.
        assert (
            await admin.client.put(
                f"{USERS}/{second_id}/role",
                headers=admin.headers(),
                json={"role": "VIEWER", **REASON},
            )
        ).status_code == 200

        # And now `admin` cannot be removed either, by anyone.
        response = await admin.client.put(
            f"{USERS}/{admin.user_id}/role",
            headers=admin.headers(),
            json={"role": "VIEWER", **REASON},
        )
        assert response.status_code == 403


class TestDeactivation:
    async def test_deactivating_signs_the_user_out(
        self,
        admin: LoggedInClient,
        second_client: AsyncClient,
        database: Database,
        settings: Settings,
    ) -> None:
        await create_test_user(database, settings, username="leaver", role=Role.VIEWER)
        await second_client.post(
            "/api/v1/auth/login",
            json={"username": "leaver", "password": TEST_PASSWORD},
        )
        assert (await second_client.get("/api/v1/auth/me")).status_code == 200

        async with database.session() as db_session:
            leaver = (
                await db_session.execute(select(User).where(User.username == "leaver"))
            ).scalar_one()
            leaver_id = leaver.id

        response = await admin.client.put(
            f"{USERS}/{leaver_id}/active",
            headers=admin.headers(),
            json={"is_active": False, **REASON},
        )
        assert response.status_code == 200
        assert (await second_client.get("/api/v1/auth/me")).status_code == 401

    async def test_cannot_deactivate_yourself(self, admin: LoggedInClient) -> None:
        response = await admin.client.put(
            f"{USERS}/{admin.user_id}/active",
            headers=admin.headers(),
            json={"is_active": False, **REASON},
        )
        assert response.status_code == 403

    async def test_deactivated_user_can_be_reactivated(self, admin: LoggedInClient) -> None:
        created = await _create(admin)
        user_id = created["user"]["id"]
        await admin.client.put(
            f"{USERS}/{user_id}/active",
            headers=admin.headers(),
            json={"is_active": False, **REASON},
        )
        response = await admin.client.put(
            f"{USERS}/{user_id}/active",
            headers=admin.headers(),
            json={"is_active": True, **REASON},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is True


class TestPasswordReset:
    async def test_reset_issues_a_working_one_time_password(
        self, admin: LoggedInClient, second_client: AsyncClient
    ) -> None:
        created = await _create(admin)
        response = await admin.client.post(
            f"{USERS}/{created['user']['id']}/password-reset",
            headers=admin.headers(),
            json=REASON,
        )
        assert response.status_code == 200
        new_password = response.json()["generated_password"]
        assert response.json()["user"]["must_change_password"] is True

        login = await second_client.post(
            "/api/v1/auth/login",
            json={"username": "newperson", "password": new_password},
        )
        assert login.status_code == 200

    async def test_reset_revokes_existing_sessions(
        self, admin: LoggedInClient, second_client: AsyncClient
    ) -> None:
        """A reset is exactly the situation where you cannot assume the
        existing sessions belong to the owner."""
        created = await _create(admin)
        await second_client.post(
            "/api/v1/auth/login",
            json={
                "username": "newperson",
                "password": created["generated_password"],
            },
        )
        assert (await second_client.get("/api/v1/auth/me")).status_code == 200

        await admin.client.post(
            f"{USERS}/{created['user']['id']}/password-reset",
            headers=admin.headers(),
            json=REASON,
        )
        assert (await second_client.get("/api/v1/auth/me")).status_code == 401


class TestUnlock:
    async def test_unlock_clears_a_lockout(
        self,
        admin: LoggedInClient,
        second_client: AsyncClient,
        database: Database,
        settings: Settings,
    ) -> None:
        await create_test_user(database, settings, username="locked", role=Role.VIEWER)
        for _ in range(settings.login_max_failed_attempts):
            await second_client.post(
                "/api/v1/auth/login",
                json={"username": "locked", "password": "wrong-password"},
            )
        blocked = await second_client.post(
            "/api/v1/auth/login",
            json={"username": "locked", "password": TEST_PASSWORD},
        )
        assert blocked.status_code == 401

        async with database.session() as db_session:
            target = (
                await db_session.execute(select(User).where(User.username == "locked"))
            ).scalar_one()
            target_id = target.id

        assert (
            await admin.client.post(
                f"{USERS}/{target_id}/unlock", headers=admin.headers(), json=REASON
            )
        ).status_code == 200

        async with database.session() as db_session:
            target = (
                await db_session.execute(select(User).where(User.username == "locked"))
            ).scalar_one()
        assert target.locked_until is None
        assert target.failed_login_count == 0


class TestNotFound:
    async def test_unknown_user_is_404(self, admin: LoggedInClient) -> None:
        response = await admin.client.get(f"{USERS}/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    async def test_malformed_uuid_is_422(self, admin: LoggedInClient) -> None:
        assert (await admin.client.get(f"{USERS}/not-a-uuid")).status_code == 422
