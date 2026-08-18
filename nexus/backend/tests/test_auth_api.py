"""Authentication endpoint tests, including the attacks they defend against."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import TEST_PASSWORD, LoggedInClient, create_test_user
from httpx import AsyncClient
from sqlalchemy import select

from nexus.core.config import Settings
from nexus.core.rbac import Role
from nexus.db.base import utcnow
from nexus.db.models.audit import AuditEvent
from nexus.db.models.user import User, UserSession
from nexus.db.session import Database

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"


class TestLogin:
    async def test_valid_credentials_start_a_session(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        response = await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["username"] == "ian"
        assert body["csrf_token"]
        assert "users:manage" in body["permissions"]

    async def test_password_is_never_echoed(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        response = await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        assert TEST_PASSWORD not in response.text
        assert "password_hash" not in response.text

    async def test_username_is_case_insensitive(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.VIEWER)
        response = await client.post(LOGIN, json={"username": "IAN", "password": TEST_PASSWORD})
        assert response.status_code == 200

    async def test_wrong_password_is_rejected(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        response = await client.post(
            LOGIN, json={"username": "ian", "password": "wrong-password-entirely"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    async def test_unknown_user_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        """Any difference here is a free account-enumeration oracle."""
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)

        wrong_password = await client.post(
            LOGIN, json={"username": "ian", "password": "wrong-password-entirely"}
        )
        no_such_user = await client.post(
            LOGIN, json={"username": "nosuchuser", "password": "wrong-password-entirely"}
        )
        assert wrong_password.status_code == no_such_user.status_code == 401
        assert (
            wrong_password.json()["error"]["message"] == (no_such_user.json()["error"]["message"])
        )

    async def test_deactivated_account_cannot_log_in(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(
            database, settings, username="gone", role=Role.OPERATOR, is_active=False
        )
        response = await client.post(LOGIN, json={"username": "gone", "password": TEST_PASSWORD})
        assert response.status_code == 401


class TestSessionCookie:
    async def test_cookie_has_the_flags_that_matter(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        response = await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        header = response.headers["set-cookie"].lower()
        # HttpOnly: an XSS bug cannot read the session.
        assert "httponly" in header
        # SameSite=Strict: the browser will not attach it to cross-site requests.
        assert "samesite=strict" in header
        assert "path=/" in header

    async def test_only_the_hash_is_stored(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        """A database dump must not hand over live sessions."""
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        cookie = client.cookies.get(settings.session_cookie_name)
        assert cookie

        async with database.session() as db_session:
            stored = (await db_session.execute(select(UserSession))).scalars().all()
        assert len(stored) == 1
        assert stored[0].token_hash != cookie
        assert len(stored[0].token_hash) == 64

    async def test_each_login_issues_a_new_session(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        """Session fixation: a session id planted before login is never the one
        used after it."""
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        first = await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        first_cookie = client.cookies.get(settings.session_cookie_name)
        second = await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        assert first.status_code == second.status_code == 200
        assert client.cookies.get(settings.session_cookie_name) != first_cookie


class TestLockout:
    async def test_account_locks_after_repeated_failures(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)

        for _ in range(settings.login_max_failed_attempts):
            await client.post(LOGIN, json={"username": "ian", "password": "wrong-password"})

        async with database.session() as db_session:
            user = (
                await db_session.execute(select(User).where(User.username == "ian"))
            ).scalar_one()
            assert user.locked_until is not None

        # Even the correct password is refused while locked.
        response = await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})
        assert response.status_code == 401
        assert "locked" in response.json()["error"]["message"].lower()

    async def test_counter_resets_after_a_successful_login(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        await client.post(LOGIN, json={"username": "ian", "password": "wrong-password"})
        await client.post(LOGIN, json={"username": "ian", "password": TEST_PASSWORD})

        async with database.session() as db_session:
            user = (
                await db_session.execute(select(User).where(User.username == "ian"))
            ).scalar_one()
        assert user.failed_login_count == 0
        assert user.last_login_at is not None

    async def test_failed_attempts_are_audited_durably(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        """A failed login raises an exception, which rolls the request's
        transaction back. The evidence must survive that anyway."""
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        await client.post(LOGIN, json={"username": "ian", "password": "wrong-password"})

        async with database.session() as db_session:
            entries = (
                (
                    await db_session.execute(
                        select(AuditEvent).where(AuditEvent.action == "LOGIN_FAILED")
                    )
                )
                .scalars()
                .all()
            )
        assert len(entries) == 1
        assert entries[0].outcome == "FAILURE"
        assert entries[0].actor_username == "ian"


class TestRateLimit:
    async def test_repeated_attempts_are_throttled(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        statuses = [
            (
                await client.post(LOGIN, json={"username": "ian", "password": "wrong-password"})
            ).status_code
            for _ in range(settings.login_rate_limit_per_minute + 3)
        ]
        assert 429 in statuses

    async def test_throttled_response_says_when_to_retry(
        self, client: AsyncClient, database: Database, settings: Settings
    ) -> None:
        await create_test_user(database, settings, username="ian", role=Role.ADMIN)
        response = None
        for _ in range(settings.login_rate_limit_per_minute + 3):
            response = await client.post(
                LOGIN, json={"username": "ian", "password": "wrong-password"}
            )
            if response.status_code == 429:
                break
        assert response is not None and response.status_code == 429
        assert int(response.headers["retry-after"]) > 0


class TestAuthenticatedEndpoints:
    async def test_me_requires_a_session(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_me_describes_the_caller(self, admin: LoggedInClient) -> None:
        response = await admin.client.get("/api/v1/auth/me")
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["username"] == "admin_user"
        assert body["user"]["role"] == "ADMIN"
        assert "password_hash" not in str(body)

    async def test_logout_revokes_the_session_immediately(self, admin: LoggedInClient) -> None:
        logout = await admin.client.post("/api/v1/auth/logout", headers=admin.headers())
        assert logout.status_code == 200
        assert (await admin.client.get("/api/v1/auth/me")).status_code == 401

    async def test_a_stolen_cookie_stops_working_after_logout(
        self, admin: LoggedInClient, settings: Settings
    ) -> None:
        """Server-side sessions are revocable — this is the whole reason they
        were chosen over JWTs."""
        stolen = admin.client.cookies.get(settings.session_cookie_name)
        await admin.client.post("/api/v1/auth/logout", headers=admin.headers())

        admin.client.cookies.set(settings.session_cookie_name, stolen)
        assert (await admin.client.get("/api/v1/auth/me")).status_code == 401

    async def test_garbage_cookie_is_rejected(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        client.cookies.set(settings.session_cookie_name, "not-a-real-token")
        assert (await client.get("/api/v1/auth/me")).status_code == 401


class TestCSRF:
    async def test_write_without_csrf_token_is_refused(self, admin: LoggedInClient) -> None:
        response = await admin.client.post("/api/v1/auth/logout", headers=admin.headers(csrf=False))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_FAILED"

    async def test_write_with_a_wrong_csrf_token_is_refused(self, admin: LoggedInClient) -> None:
        response = await admin.client.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": "forged-token-value"}
        )
        assert response.status_code == 403

    async def test_reads_do_not_need_a_csrf_token(self, admin: LoggedInClient) -> None:
        assert (
            await admin.client.get("/api/v1/auth/me", headers=admin.headers(csrf=False))
        ).status_code == 200

    async def test_the_token_is_not_in_a_readable_cookie(
        self, admin: LoggedInClient, settings: Settings
    ) -> None:
        cookie_names = set(admin.client.cookies.keys())
        assert cookie_names == {settings.session_cookie_name}


class TestSessionManagement:
    async def test_lists_own_sessions(self, admin: LoggedInClient) -> None:
        response = await admin.client.get("/api/v1/auth/sessions")
        assert response.status_code == 200
        sessions = response.json()
        assert len(sessions) == 1
        assert sessions[0]["current"] is True

    async def test_cannot_revoke_another_users_session(
        self,
        admin: LoggedInClient,
        client: AsyncClient,
        database: Database,
        settings: Settings,
    ) -> None:
        """And the refusal looks identical to 'no such session', so the
        endpoint cannot be used to confirm other users' session ids."""
        await create_test_user(database, settings, username="other", role=Role.VIEWER)
        async with database.session() as db_session:
            other = (
                await db_session.execute(select(User).where(User.username == "other"))
            ).scalar_one()
            record = UserSession(
                user_id=other.id,
                token_hash="c" * 64,
                csrf_token_hash="d" * 64,
                expires_at=_one_hour_from_now(),
            )
            db_session.add(record)
            await db_session.flush()
            other_session_id = record.id

        response = await admin.client.delete(
            f"/api/v1/auth/sessions/{other_session_id}", headers=admin.headers()
        )
        assert response.status_code == 404


class TestPasswordChange:
    async def test_changes_password_and_keeps_current_session(self, admin: LoggedInClient) -> None:
        response = await admin.client.post(
            "/api/v1/auth/password",
            headers=admin.headers(),
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "a-brand-new-passphrase-42",
            },
        )
        assert response.status_code == 200
        assert (await admin.client.get("/api/v1/auth/me")).status_code == 200

    async def test_wrong_current_password_is_refused(self, admin: LoggedInClient) -> None:
        """Stops someone with a stolen session — but not the password — from
        locking the owner out."""
        response = await admin.client.post(
            "/api/v1/auth/password",
            headers=admin.headers(),
            json={
                "current_password": "not-the-current-password",
                "new_password": "a-brand-new-passphrase-42",
            },
        )
        assert response.status_code == 401

    async def test_weak_new_password_is_refused(self, admin: LoggedInClient) -> None:
        response = await admin.client.post(
            "/api/v1/auth/password",
            headers=admin.headers(),
            json={"current_password": TEST_PASSWORD, "new_password": "short"},
        )
        assert response.status_code == 422

    async def test_other_sessions_are_revoked(
        self, admin: LoggedInClient, second_client: AsyncClient
    ) -> None:
        # A second browser signed in as the same account.
        login = await second_client.post(
            LOGIN, json={"username": admin.username, "password": TEST_PASSWORD}
        )
        assert login.status_code == 200

        await admin.client.post(
            "/api/v1/auth/password",
            headers=admin.headers(),
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "a-brand-new-passphrase-42",
            },
        )
        assert (await second_client.get("/api/v1/auth/me")).status_code == 401


def _one_hour_from_now():
    return utcnow() + timedelta(hours=1)
