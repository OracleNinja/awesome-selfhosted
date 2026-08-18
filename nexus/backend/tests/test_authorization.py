"""Authorization tests: the permission matrix, and its enforcement at the edge.

Two layers are tested separately on purpose:

* the **matrix** is pure logic and needs no database — if VIEWER ever gains
  `devices:quarantine`, that is a one-line test failure;
* the **enforcement** is what actually protects the system — a correct matrix
  that no endpoint consults protects nothing.
"""

from __future__ import annotations

import pytest
from conftest import LoggedInClient
from sqlalchemy import select

from nexus.core.rbac import (
    SENSITIVE_PERMISSIONS,
    Permission,
    Role,
    has_permission,
    permissions_for,
    requires_confirmation,
)
from nexus.db.models.audit import AuditEvent
from nexus.db.session import Database


class TestPermissionMatrix:
    """No database, no HTTP — just the policy."""

    def test_viewer_can_only_read(self) -> None:
        viewer = permissions_for(Role.VIEWER)
        assert Permission.EVENTS_READ in viewer
        assert Permission.DEVICES_READ in viewer
        for permission in (
            Permission.DEVICES_QUARANTINE,
            Permission.RULES_WRITE,
            Permission.USERS_MANAGE,
            Permission.EVIDENCE_DELETE,
            Permission.DETECTIONS_TRIAGE,
        ):
            assert permission not in viewer

    def test_operator_can_respond_but_not_administer(self) -> None:
        operator = permissions_for(Role.OPERATOR)
        # Responding to an active threat is the operator's job.
        assert Permission.DEVICES_QUARANTINE in operator
        assert Permission.DETECTIONS_TRIAGE in operator
        # Changing what the platform detects, or who can use it, is not.
        for permission in (
            Permission.RULES_WRITE,
            Permission.USERS_MANAGE,
            Permission.AUTH_SETTINGS_WRITE,
            Permission.EVIDENCE_DELETE,
            Permission.AUDIT_READ,
        ):
            assert permission not in operator

    def test_admin_holds_every_permission(self) -> None:
        assert permissions_for(Role.ADMIN) == frozenset(Permission)

    def test_roles_are_strictly_nested(self) -> None:
        """Each role is a superset of the one below, so a promotion never
        removes a capability."""
        viewer = permissions_for(Role.VIEWER)
        operator = permissions_for(Role.OPERATOR)
        admin = permissions_for(Role.ADMIN)
        assert viewer < operator < admin

    def test_unknown_role_gets_nothing(self) -> None:
        """Fail closed: a role this build does not recognise — after a
        downgrade, say — must not be interpreted generously."""
        assert permissions_for("SUPERUSER") == frozenset()  # type: ignore[arg-type]
        assert has_permission("SUPERUSER", Permission.EVENTS_READ) is False  # type: ignore[arg-type]

    def test_destructive_permissions_require_confirmation(self) -> None:
        for permission in (
            Permission.DEVICES_QUARANTINE,
            Permission.EVIDENCE_DELETE,
            Permission.USERS_MANAGE,
            Permission.RULES_WRITE,
        ):
            assert requires_confirmation(permission) is True

    def test_read_permissions_do_not_require_confirmation(self) -> None:
        assert requires_confirmation(Permission.EVENTS_READ) is False

    def test_sensitive_permissions_are_a_subset_of_all_permissions(self) -> None:
        assert frozenset(Permission) >= SENSITIVE_PERMISSIONS


@pytest.mark.integration
class TestEnforcement:
    """The matrix means nothing unless endpoints consult it."""

    async def test_viewer_cannot_list_users(self, viewer: LoggedInClient) -> None:
        response = await viewer.client.get("/api/v1/users")
        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "PERMISSION_DENIED"
        # Tells the operator what they would need, without leaking anything.
        assert body["error"]["details"]["required_permission"] == "users:manage"
        assert body["error"]["details"]["your_role"] == "VIEWER"

    async def test_operator_cannot_list_users(self, operator: LoggedInClient) -> None:
        assert (await operator.client.get("/api/v1/users")).status_code == 403

    async def test_admin_can_list_users(self, admin: LoggedInClient) -> None:
        assert (await admin.client.get("/api/v1/users")).status_code == 200

    async def test_operator_cannot_read_the_audit_log(self, operator: LoggedInClient) -> None:
        assert (await operator.client.get("/api/v1/audit")).status_code == 403

    async def test_admin_can_read_the_audit_log(self, admin: LoggedInClient) -> None:
        assert (await admin.client.get("/api/v1/audit")).status_code == 200

    async def test_anonymous_callers_get_401_not_403(self, client) -> None:
        """401 means 'who are you', 403 means 'not you'. Returning 403 to an
        anonymous caller would confirm the endpoint exists and is protected."""
        assert (await client.get("/api/v1/users")).status_code == 401

    async def test_denials_are_audited(self, viewer: LoggedInClient, database: Database) -> None:
        """Refused attempts are the interesting ones: a VIEWER repeatedly
        trying to reach admin endpoints is a signal."""
        await viewer.client.get("/api/v1/users")

        async with database.session() as db_session:
            denials = (
                (
                    await db_session.execute(
                        select(AuditEvent).where(AuditEvent.action == "PERMISSION_DENIED")
                    )
                )
                .scalars()
                .all()
            )
        assert len(denials) == 1
        entry = denials[0]
        assert entry.outcome == "DENIED"
        assert entry.actor_username == "viewer_user"
        assert entry.target_id == "users:manage"
        assert entry.details["path"] == "/api/v1/users"

    async def test_denial_audit_survives_the_rejection(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        """The 403 aborts the request transaction; the evidence must not be
        rolled back with it."""
        for _ in range(3):
            await viewer.client.get("/api/v1/users")

        async with database.session() as db_session:
            count = len(
                (
                    await db_session.execute(
                        select(AuditEvent).where(AuditEvent.action == "PERMISSION_DENIED")
                    )
                )
                .scalars()
                .all()
            )
        assert count == 3

    async def test_permissions_reported_at_login_match_the_matrix(
        self, operator: LoggedInClient
    ) -> None:
        """The UI hides what the user cannot do, but only the server decides."""
        response = await operator.client.get("/api/v1/auth/me")
        reported = set(response.json()["permissions"])
        expected = {item.value for item in permissions_for(Role.OPERATOR)}
        assert reported == expected
