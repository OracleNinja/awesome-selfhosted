"""Health endpoint tests, including the database-down path.

The failure tests here matter more than the success ones: anyone can make a
health check return 200 when everything works. What decides whether an outage
is survivable is what happens when the database is gone.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from nexus.core.context import AppContext
from nexus.db.session import Database, DatabaseHealth

pytestmark = pytest.mark.integration


class TestLiveness:
    async def test_reports_alive(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health/live")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "alive"
        assert body["uptime_seconds"] >= 0
        assert body["version"]

    async def test_stays_alive_when_the_database_is_down(
        self, client: AsyncClient, context: AppContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Liveness must not depend on the database.

        If it did, a database outage would make an orchestrator kill and
        restart every application container — turning one broken dependency
        into a restart storm that makes recovery harder.
        """

        async def broken(self: Database) -> DatabaseHealth:
            return DatabaseHealth(ok=False, error="OSError: connection refused")

        monkeypatch.setattr(Database, "check_health", broken)
        response = await client.get("/api/v1/health/live")
        assert response.status_code == 200


class TestReadiness:
    async def test_ready_when_dependencies_are_up(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["status"] == "ok"
        names = {component["name"] for component in body["components"]}
        # The database is required; the queue and worker are reported alongside
        # it, and sensors appear per configured sensor.
        assert {"database", "job_queue", "worker"} <= names

    async def test_returns_503_when_the_database_is_unreachable(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def broken(self: Database) -> DatabaseHealth:
            return DatabaseHealth(ok=False, error="OSError: connection refused")

        monkeypatch.setattr(Database, "check_health", broken)
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        # Still the normal body shape, not an error envelope: a probe needs to
        # see *which* component failed.
        database = next(c for c in body["components"] if c["name"] == "database")
        assert database["status"] == "unavailable"

    async def test_recovers_without_a_restart(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Readiness is re-evaluated on every probe, so a recovered database
        brings the instance back into rotation by itself."""
        calls = {"n": 0}
        real = Database.check_health

        async def flaky(self: Database) -> DatabaseHealth:
            calls["n"] += 1
            if calls["n"] == 1:
                return DatabaseHealth(ok=False, error="temporary")
            return await real(self)

        monkeypatch.setattr(Database, "check_health", flaky)
        assert (await client.get("/api/v1/health/ready")).status_code == 503
        assert (await client.get("/api/v1/health/ready")).status_code == 200


class TestHealthSummary:
    async def test_always_returns_200_so_failures_are_distinguishable(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'NEXUS says the database is down' and 'NEXUS is unreachable' are
        different incidents, so this endpoint answers even while degraded."""

        async def broken(self: Database) -> DatabaseHealth:
            return DatabaseHealth(ok=False, error="OSError: connection refused")

        monkeypatch.setattr(Database, "check_health", broken)
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"

    async def test_does_not_leak_the_connection_string(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def leaky(self: Database) -> DatabaseHealth:
            return DatabaseHealth(
                ok=False,
                error="could not connect to postgresql+asyncpg://nexus:hunter2@db/nexus",
            )

        monkeypatch.setattr(Database, "check_health", leaky)
        response = await client.get("/api/v1/health")
        assert "hunter2" not in response.text


class TestResponseHeaders:
    async def test_request_id_header_is_present(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health/live")
        assert response.headers["x-request-id"]

    async def test_supplied_request_id_is_echoed(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/health/live", headers={"X-Request-ID": "trace-abc-123"}
        )
        assert response.headers["x-request-id"] == "trace-abc-123"

    async def test_malformed_request_id_is_replaced_not_echoed(self, client: AsyncClient) -> None:
        """Echoing arbitrary client input into log lines is log injection."""
        response = await client.get(
            "/api/v1/health/live", headers={"X-Request-ID": "bad\nvalue injected"}
        )
        assert response.headers["x-request-id"] != "bad\nvalue injected"

    async def test_security_headers_are_applied(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health/live")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "no-referrer"

    async def test_hsts_is_absent_outside_production(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health/live")
        assert "strict-transport-security" not in response.headers
