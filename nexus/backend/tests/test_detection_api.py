"""Detections, evidence, risk and configuration over HTTP.

The assertions here are about three things the specification is explicit about:
authorization is enforced per endpoint, evidence and contributing factors are
actually retrievable, and every state the API reports is honest — a rule that
cannot run says so, an unassessed device is not a zero, and a system that has
finished its work is idle rather than unavailable.
"""

from __future__ import annotations

import pytest
from conftest import LoggedInClient
from httpx import AsyncClient
from sqlalchemy import select

from nexus.db.models.audit import AuditEvent
from nexus.db.models.device import Device
from nexus.db.models.finding import Finding
from nexus.db.session import Database
from nexus.detection.engine import DetectionEngine
from tests.detection_helpers import discovery, flow, ingest, make_sensor, minutes_ago, scan

pytestmark = pytest.mark.integration


async def seed_findings(database: Database, settings, *, simulation: bool = False) -> None:
    """Produce real findings through the real pipeline."""
    async with database.session() as session:
        sensor = await make_sensor(
            session,
            name="lab" if simulation else "real",
            driver="lab_synthetic" if simulation else "packet_capture",
            simulation=simulation,
        )
        prefix = "192.0.2" if simulation else "192.168.1"
        mac = "aa:bb:cc:77:00:01" if simulation else "aa:bb:cc:88:00:01"
        await ingest(session, sensor, [discovery(mac=mac, ip=f"{prefix}.40")])
        await ingest(
            session,
            sensor,
            [
                scan(mac=mac, ip=f"{prefix}.40", index=index, at=minutes_ago(2))
                for index in range(6)
            ],
        )
        await ingest(
            session,
            sensor,
            [
                flow(
                    mac=mac,
                    source=f"{prefix}.40",
                    destination="203.0.113.55" if not simulation else "198.51.100.7",
                    port=4444,
                )
            ],
        )
        await DetectionEngine(session, settings).analyze_pending()


class TestAuthorization:
    async def test_findings_require_authentication(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/detections")
        assert response.status_code == 401

    async def test_viewer_may_read_findings(self, viewer: LoggedInClient) -> None:
        response = await viewer.client.get("/api/v1/detections", headers=viewer.headers())
        assert response.status_code == 200

    async def test_viewer_may_not_triage(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (await session.execute(select(Finding).limit(1))).scalar_one()
            finding_id = str(finding.id)

        response = await viewer.client.post(
            f"/api/v1/detections/{finding_id}/status",
            headers=viewer.headers(),
            json={"status": "ACKNOWLEDGED", "note": "looks fine to me"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    async def test_operator_may_triage(
        self, operator: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (await session.execute(select(Finding).limit(1))).scalar_one()
            finding_id = str(finding.id)

        response = await operator.client.post(
            f"/api/v1/detections/{finding_id}/status",
            headers=operator.headers(),
            json={"status": "ACKNOWLEDGED", "note": "investigating"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ACKNOWLEDGED"

    async def test_operator_may_not_change_the_configuration(
        self, operator: LoggedInClient
    ) -> None:
        response = await operator.client.put(
            "/api/v1/detections/config",
            headers=operator.headers(),
            json={"config": {"weights": {"recent_activity": 20}}, "reason": "tuning"},
        )
        assert response.status_code == 403

    async def test_triage_requires_a_csrf_token(
        self, operator: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (await session.execute(select(Finding).limit(1))).scalar_one()
            finding_id = str(finding.id)

        response = await operator.client.post(
            f"/api/v1/detections/{finding_id}/status",
            headers=operator.headers(csrf=False),
            json={"status": "ACKNOWLEDGED", "note": "investigating"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_FAILED"


class TestFindingQueries:
    async def test_lists_findings_with_both_axes(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        response = await viewer.client.get("/api/v1/detections", headers=viewer.headers())
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 3

        item = body["items"][0]
        assert item["severity"] in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert 0 <= item["confidence"] <= 100
        assert item["explanation"]
        assert item["detector"]
        assert item["rule_version"] == 1

    async def test_unknown_filter_value_is_rejected_not_emptied(
        self, viewer: LoggedInClient
    ) -> None:
        response = await viewer.client.get(
            "/api/v1/detections?severity=SEVERE", headers=viewer.headers()
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"

    async def test_simulated_findings_are_excluded_by_default(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings, simulation=True)

        default = await viewer.client.get("/api/v1/detections", headers=viewer.headers())
        assert default.json()["total"] == 0

        laboratory = await viewer.client.get(
            "/api/v1/detections?only_simulation=true", headers=viewer.headers()
        )
        assert laboratory.json()["total"] >= 1
        assert all(item["is_simulation"] for item in laboratory.json()["items"])

    async def test_filter_by_detection_type(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        response = await viewer.client.get(
            "/api/v1/detections?detection_type=REPEATED_ANOMALY", headers=viewer.headers()
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_missing_finding_is_a_404(self, viewer: LoggedInClient) -> None:
        response = await viewer.client.get(
            "/api/v1/detections/00000000-0000-0000-0000-000000000000",
            headers=viewer.headers(),
        )
        assert response.status_code == 404


class TestEvidence:
    async def test_evidence_names_the_sensor_and_the_raw_event(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (
                await session.execute(
                    select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
                )
            ).scalar_one()
            finding_id = str(finding.id)

        response = await viewer.client.get(
            f"/api/v1/detections/{finding_id}/evidence", headers=viewer.headers()
        )
        assert response.status_code == 200
        body = response.json()

        assert body["evidence_state"] == "COMPLETE"
        assert body["observations_recorded"] == body["observations_available"]
        assert body["observations"]

        first = body["observations"][0]
        # The three provenance questions, answerable from the API.
        assert first["sensor_name"] == "real-sensor" or first["sensor_name"] == "real"
        assert first["occurred_at"]
        assert first["event_id"]
        assert "raw_retained" in first
        assert first["role"] in {"TRIGGER", "SUPPORTING", "BASELINE"}

    async def test_evidence_reports_pruning_rather_than_hiding_it(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (
                await session.execute(
                    select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
                )
            ).scalar_one()
            finding_id = str(finding.id)
            # Simulate retention removing the underlying events.
            from nexus.db.models.event import SecurityEvent

            await session.execute(SecurityEvent.__table__.delete())

        response = await viewer.client.get(
            f"/api/v1/detections/{finding_id}/evidence", headers=viewer.headers()
        )
        body = response.json()
        assert body["evidence_state"] == "PRUNED"
        assert body["observations_available"] == 0
        assert body["observations_recorded"] > 0
        # The finding is still explainable from its stored snapshot.
        assert body["snapshot"]
        assert body["explanation"]


class TestRisk:
    async def test_risk_response_shows_every_factor(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            device = (await session.execute(select(Device))).scalar_one()
            device_id = str(device.id)

        response = await viewer.client.get(
            f"/api/v1/devices/{device_id}/risk", headers=viewer.headers()
        )
        assert response.status_code == 200
        body = response.json()

        assert body["state"] == "ASSESSED"
        assert body["risk_score"] is not None
        assert body["risk_level"] != "UNKNOWN"
        assert len(body["factors"]) == 7
        assert body["window_started_at"] and body["window_ended_at"]
        assert body["weights_source"] == "DEFAULT"
        assert body["contributing_findings"]

        # The arithmetic is checkable from the response alone.
        total = sum(factor["contribution"] for factor in body["factors"])
        assert abs(round(total) - body["risk_score"]) <= 1

    async def test_unassessed_device_is_not_reported_as_zero(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        async with database.session() as session:
            sensor = await make_sensor(session)
            await ingest(session, sensor, [discovery(mac="aa:bb:cc:66:00:01")])
            device = (await session.execute(select(Device))).scalar_one()
            device_id = str(device.id)

        response = await viewer.client.get(
            f"/api/v1/devices/{device_id}/risk", headers=viewer.headers()
        )
        body = response.json()
        assert body["state"] == "NOT_ASSESSED"
        assert body["risk_score"] is None
        assert body["risk_level"] == "UNKNOWN"
        assert body["factors"] == []

    async def test_risk_history_records_movement(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            device = (await session.execute(select(Device))).scalar_one()
            device_id = str(device.id)

        response = await viewer.client.get(
            f"/api/v1/devices/{device_id}/risk/assessments", headers=viewer.headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert body["items"][0]["weights_fingerprint"]


class TestTriage:
    async def test_triage_lowers_risk_and_is_audited(
        self, operator: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            device = (await session.execute(select(Device))).scalar_one()
            device_id = str(device.id)
            before = device.risk_score
            findings = (await session.execute(select(Finding))).scalars().all()
            ids = [str(item.id) for item in findings]

        for finding_id in ids:
            response = await operator.client.post(
                f"/api/v1/detections/{finding_id}/status",
                headers=operator.headers(),
                json={"status": "RESOLVED", "note": "handled during the maintenance window"},
            )
            assert response.status_code == 200

        risk = await operator.client.get(
            f"/api/v1/devices/{device_id}/risk", headers=operator.headers()
        )
        assert risk.json()["risk_score"] < before

        async with database.session() as session:
            actions = (await session.execute(select(AuditEvent.action))).scalars().all()
            assert "DETECTION_FINDING_TRIAGED" in actions

    async def test_triage_to_the_same_status_is_a_conflict(
        self, operator: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (await session.execute(select(Finding).limit(1))).scalar_one()
            finding_id = str(finding.id)

        payload = {"status": "OPEN", "note": "already open"}
        response = await operator.client.post(
            f"/api/v1/detections/{finding_id}/status",
            headers=operator.headers(),
            json=payload,
        )
        assert response.status_code == 409

    async def test_note_is_required(
        self, operator: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings)
        async with database.session() as session:
            finding = (await session.execute(select(Finding).limit(1))).scalar_one()
            finding_id = str(finding.id)

        response = await operator.client.post(
            f"/api/v1/detections/{finding_id}/status",
            headers=operator.headers(),
            json={"status": "RESOLVED"},
        )
        assert response.status_code == 422


class TestStatus:
    async def test_reports_idle_when_caught_up(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        """A finished workload is idle, not unavailable."""
        await seed_findings(database, settings)

        response = await viewer.client.get("/api/v1/detections/status", headers=viewer.headers())
        assert response.status_code == 200
        body = response.json()

        assert body["analysis"]["state"] == "IDLE"
        assert body["analysis"]["pending_events"] == 0
        assert body["analysis"]["last_run_status"] == "SUCCEEDED"
        assert body["analysis"]["events_examined_total"] > 0

    async def test_reports_never_run_before_any_analysis(self, viewer: LoggedInClient) -> None:
        response = await viewer.client.get("/api/v1/detections/status", headers=viewer.headers())
        body = response.json()
        assert body["analysis"]["state"] == "NEVER_RUN"
        assert body["analysis"]["watermark_event_id"] == 0

    async def test_detectors_are_listed_per_scope_with_their_state(
        self, viewer: LoggedInClient
    ) -> None:
        response = await viewer.client.get("/api/v1/detections/status", headers=viewer.headers())
        body = response.json()
        # Four rules, two scopes each.
        assert len(body["detectors"]) == 8
        assert {row["scope"] for row in body["detectors"]} == {"REAL_NETWORK", "SIMULATION"}
        assert all(
            row["state"] in {"ACTIVE", "DISABLED", "NOT_CONFIGURED"} for row in body["detectors"]
        )

    async def test_unassessed_devices_are_counted_separately(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        async with database.session() as session:
            sensor = await make_sensor(session)
            await ingest(session, sensor, [discovery(mac="aa:bb:cc:55:00:01")])

        response = await viewer.client.get("/api/v1/detections/status", headers=viewer.headers())
        body = response.json()
        assert body["risk"]["devices_not_assessed"] == 1
        assert body["risk"]["devices_assessed"] == 0

    async def test_simulation_and_real_findings_are_counted_apart(
        self, viewer: LoggedInClient, database: Database, settings
    ) -> None:
        await seed_findings(database, settings, simulation=True)
        response = await viewer.client.get("/api/v1/detections/status", headers=viewer.headers())
        body = response.json()
        assert body["findings_simulation"] > 0
        assert body["findings_real"] == 0


class TestConfigurationApi:
    async def test_reads_the_defaults(self, viewer: LoggedInClient) -> None:
        response = await viewer.client.get("/api/v1/detections/config", headers=viewer.headers())
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "DEFAULT"
        assert body["version"] is None
        assert body["config"]["weights"]["severity_weighted_findings"] == 45.0
        assert body["config"]["rules"]["repeated_anomaly"]["window_seconds"] == 900

    async def test_admin_can_tune_and_it_is_audited(
        self, admin: LoggedInClient, database: Database
    ) -> None:
        response = await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={
                "config": {"rules": {"repeated_anomaly": {"min_occurrences": 3}}},
                "reason": "this network is quiet enough for a lower threshold",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "CONFIGURED"
        assert body["version"] == 1
        assert body["config"]["rules"]["repeated_anomaly"]["min_occurrences"] == 3
        # Merged, not replaced.
        assert body["config"]["rules"]["repeated_anomaly"]["window_seconds"] == 900
        assert body["config"]["weights"]["severity_weighted_findings"] == 45.0

        async with database.session() as session:
            actions = (await session.execute(select(AuditEvent.action))).scalars().all()
            assert "DETECTION_CONFIG_CHANGED" in actions

    async def test_reason_is_required(self, admin: LoggedInClient) -> None:
        response = await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={"config": {"weights": {"recent_activity": 20}}},
        )
        assert response.status_code == 422

    async def test_invalid_values_are_rejected(self, admin: LoggedInClient) -> None:
        response = await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={
                "config": {"rules": {"repeated_anomaly": {"window_seconds": 1}}},
                "reason": "too small",
            },
        )
        assert response.status_code == 422

    async def test_unknown_keys_are_rejected(self, admin: LoggedInClient) -> None:
        response = await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={"config": {"weights": {"vibes": 10}}, "reason": "nope"},
        )
        assert response.status_code == 422

    async def test_stale_version_is_a_conflict(self, admin: LoggedInClient) -> None:
        await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={"config": {"weights": {"recent_activity": 20}}, "reason": "first"},
        )
        response = await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={
                "config": {"weights": {"recent_activity": 25}},
                "version": 0,
                "reason": "second, with a stale version",
            },
        )
        assert response.status_code == 409

    async def test_stored_configuration_takes_effect(
        self, admin: LoggedInClient, database: Database, settings
    ) -> None:
        await admin.client.put(
            "/api/v1/detections/config",
            headers=admin.headers(),
            json={
                "config": {"rules": {"repeated_anomaly": {"min_occurrences": 3}}},
                "reason": "lower the threshold",
            },
        )

        async with database.session() as session:
            sensor = await make_sensor(session, driver="packet_capture")
            await ingest(
                session,
                sensor,
                [scan(index=index, at=minutes_ago(2)) for index in range(3)],
            )
            await DetectionEngine(session, settings).analyze_pending()

        response = await admin.client.get(
            "/api/v1/detections?detection_type=REPEATED_ANOMALY", headers=admin.headers()
        )
        assert response.json()["total"] == 1


class TestOpenApi:
    async def test_endpoints_are_published(self, client: AsyncClient) -> None:
        document = (await client.get("/api/openapi.json")).json()
        paths = document["paths"]
        assert "/api/v1/detections" in paths
        assert "/api/v1/detections/{finding_id}" in paths
        assert "/api/v1/detections/{finding_id}/evidence" in paths
        assert "/api/v1/detections/status" in paths
        assert "/api/v1/detections/config" in paths
        assert "/api/v1/devices/{device_id}/risk" in paths
