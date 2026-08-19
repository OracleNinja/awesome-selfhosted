"""Events, devices, sensors and jobs over HTTP.

The load-bearing assertions here are about honesty: simulated data must not
appear in a real-network view by default, an unconfigured sensor must say so
rather than looking quiet, and an unassessed risk score must not read as zero.
"""

from __future__ import annotations

import pytest
from conftest import LoggedInClient
from httpx import AsyncClient

from nexus.db.models.sensor import Sensor as SensorRow
from nexus.db.session import Database
from nexus.sensors.base import RawObservation
from nexus.services.ingest import IngestionService

pytestmark = pytest.mark.integration


async def seed_events(
    database: Database, *, count: int = 3, simulation: bool = False, prefix: str = "real"
) -> None:
    async with database.session() as session:
        sensor = SensorRow(
            name=f"{prefix}-sensor",
            driver="lab_synthetic" if simulation else "arp_discovery",
            config={},
            is_simulation=simulation,
        )
        session.add(sensor)
        await session.flush()
        await IngestionService(session).ingest(
            [
                RawObservation(
                    kind="device.seen",
                    category="DISCOVERY",
                    summary=f"{prefix} event {index}",
                    severity="INFO",
                    source_ip="192.0.2.5" if simulation else "192.168.1.5",
                    mac_address=f"aa:bb:cc:00:{'11' if simulation else '22'}:{index:02x}",
                    uid_seed=f"{prefix}-{index}",
                )
                for index in range(count)
            ],
            sensor,
            driver_is_simulation=simulation,
        )


class TestEventQueries:
    async def test_lists_events(self, viewer: LoggedInClient, database: Database) -> None:
        await seed_events(database, count=3)
        response = await viewer.client.get("/api/v1/events")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 3
        assert body["has_more"] is False

    async def test_simulated_events_are_excluded_by_default(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        """Laboratory data is real data about an isolated environment, but it is
        not an observation of the operator's network. Mixing the two silently
        would be the single most dishonest thing this system could do."""
        await seed_events(database, count=2, prefix="real")
        await seed_events(database, count=5, simulation=True, prefix="lab")

        default_view = (await viewer.client.get("/api/v1/events")).json()
        assert len(default_view["items"]) == 2
        assert all(item["is_simulation"] is False for item in default_view["items"])

    async def test_simulation_can_be_requested_explicitly(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        await seed_events(database, count=2, prefix="real")
        await seed_events(database, count=5, simulation=True, prefix="lab")

        both = (await viewer.client.get("/api/v1/events?include_simulation=true")).json()
        only_lab = (await viewer.client.get("/api/v1/events?only_simulation=true")).json()

        assert len(both["items"]) == 7
        assert len(only_lab["items"]) == 5
        assert all(item["is_simulation"] is True for item in only_lab["items"])

    async def test_keyset_pagination(self, viewer: LoggedInClient, database: Database) -> None:
        await seed_events(database, count=10)
        first = (await viewer.client.get("/api/v1/events?limit=4")).json()
        assert first["has_more"] is True
        assert first["next_cursor"] is not None

        second = (
            await viewer.client.get(f"/api/v1/events?limit=4&before_id={first['next_cursor']}")
        ).json()
        first_ids = {item["id"] for item in first["items"]}
        second_ids = {item["id"] for item in second["items"]}
        assert not (first_ids & second_ids), "pages overlapped"

    async def test_unknown_filter_value_is_rejected_not_silently_empty(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        """An empty result for a typo'd filter reads as 'your network is
        quiet', which is the wrong answer to give a security operator."""
        await seed_events(database, count=2)
        response = await viewer.client.get("/api/v1/events?category=TYPO")
        assert response.status_code == 422
        assert "allowed" in response.json()["error"]["details"]

    async def test_filters_work(self, viewer: LoggedInClient, database: Database) -> None:
        await seed_events(database, count=3)
        by_category = (await viewer.client.get("/api/v1/events?category=DISCOVERY")).json()
        assert len(by_category["items"]) == 3
        by_kind = (await viewer.client.get("/api/v1/events?kind=nothing.matches")).json()
        assert by_kind["items"] == []

    async def test_search_wildcards_are_escaped(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        """A user searching for '%' should not match every row."""
        await seed_events(database, count=3)
        response = await viewer.client.get("/api/v1/events?search=%25")
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_event_detail_includes_the_raw_observation(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        await seed_events(database, count=1)
        listing = (await viewer.client.get("/api/v1/events")).json()
        event_id = listing["items"][0]["id"]

        detail = (await viewer.client.get(f"/api/v1/events/{event_id}")).json()
        assert "attributes" in detail
        assert "raw" in detail

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/events")).status_code == 401


class TestDeviceApi:
    async def test_lists_devices_excluding_simulation(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        await seed_events(database, count=2, prefix="real")
        await seed_events(database, count=2, simulation=True, prefix="lab")

        body = (await viewer.client.get("/api/v1/devices")).json()
        assert body["total"] == 2
        assert all(item["is_simulation"] is False for item in body["items"])

    async def test_unassessed_risk_is_null_not_zero(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        """'Not assessed' and 'assessed and found safe' are different claims,
        and a security dashboard must not conflate them."""
        await seed_events(database, count=1)
        device = (await viewer.client.get("/api/v1/devices")).json()["items"][0]
        assert device["risk_score"] is None
        assert device["risk_level"] == "UNKNOWN"

    async def test_vendor_is_null_when_no_oui_database_is_loaded(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        """Null means 'we have no OUI data', not 'unknown manufacturer'. A
        guess here would be indistinguishable from evidence."""
        await seed_events(database, count=1)
        device = (await viewer.client.get("/api/v1/devices")).json()["items"][0]
        assert device["vendor"] is None

    async def test_device_profile_includes_recent_events(
        self, viewer: LoggedInClient, database: Database
    ) -> None:
        await seed_events(database, count=3)
        device_id = (await viewer.client.get("/api/v1/devices")).json()["items"][0]["id"]

        profile = (await viewer.client.get(f"/api/v1/devices/{device_id}")).json()
        assert profile["recent_events"]
        assert profile["event_count_24h"] >= 1

    async def test_viewer_cannot_annotate(self, viewer: LoggedInClient, database: Database) -> None:
        await seed_events(database, count=1)
        device_id = (await viewer.client.get("/api/v1/devices")).json()["items"][0]["id"]

        response = await viewer.client.patch(
            f"/api/v1/devices/{device_id}",
            headers=viewer.headers(),
            json={"label": "should not work"},
        )
        assert response.status_code == 403

    async def test_operator_can_annotate_and_it_is_audited(
        self, operator: LoggedInClient, database: Database
    ) -> None:
        from sqlalchemy import select

        from nexus.db.models.audit import AuditEvent

        await seed_events(database, count=1)
        device_id = (await operator.client.get("/api/v1/devices")).json()["items"][0]["id"]

        response = await operator.client.patch(
            f"/api/v1/devices/{device_id}",
            headers=operator.headers(),
            json={"label": "Kitchen thermostat", "is_trusted": True, "tags": ["iot"]},
        )
        assert response.status_code == 200
        assert response.json()["label"] == "Kitchen thermostat"
        assert response.json()["is_trusted"] is True

        async with database.session() as session:
            entries = (
                (
                    await session.execute(
                        select(AuditEvent).where(AuditEvent.target_type == "device")
                    )
                )
                .scalars()
                .all()
            )
        assert len(entries) == 1
        assert entries[0].details["changes"]["is_trusted"]["to"] is True

    async def test_annotation_requires_csrf(
        self, operator: LoggedInClient, database: Database
    ) -> None:
        await seed_events(database, count=1)
        device_id = (await operator.client.get("/api/v1/devices")).json()["items"][0]["id"]
        response = await operator.client.patch(
            f"/api/v1/devices/{device_id}",
            headers=operator.headers(csrf=False),
            json={"label": "forged"},
        )
        assert response.status_code == 403


class TestSensorApi:
    async def test_driver_catalogue_reports_real_availability(self, viewer: LoggedInClient) -> None:
        drivers = {
            item["driver"]: item
            for item in (await viewer.client.get("/api/v1/sensors/drivers")).json()
        }

        assert drivers["arp_discovery"]["availability"] == "AVAILABLE"
        # Not configured is a distinct state from not available, and carries a
        # remedy an operator can act on.
        assert drivers["packet_capture"]["availability"] == "NOT_CONFIGURED"
        assert drivers["packet_capture"]["remedy"]
        assert drivers["lab_synthetic"]["produces_simulated_data"] is True

    async def test_viewer_cannot_create_a_sensor(self, viewer: LoggedInClient) -> None:
        response = await viewer.client.post(
            "/api/v1/sensors",
            headers=viewer.headers(),
            json={"name": "nope", "driver": "arp_discovery"},
        )
        assert response.status_code == 403

    async def test_admin_creates_a_sensor_with_validated_config(
        self, admin: LoggedInClient
    ) -> None:
        response = await admin.client.post(
            "/api/v1/sensors",
            headers=admin.headers(),
            json={
                "name": "arp",
                "driver": "arp_discovery",
                "config": {"poll_seconds": 60},
            },
        )
        assert response.status_code == 201
        assert response.json()["config"]["poll_seconds"] == 60
        assert response.json()["is_simulation"] is False

    async def test_invalid_driver_config_is_rejected_before_storage(
        self, admin: LoggedInClient
    ) -> None:
        """A sensor must never be persisted in a state that cannot start."""
        response = await admin.client.post(
            "/api/v1/sensors",
            headers=admin.headers(),
            json={"name": "bad", "driver": "arp_discovery", "config": {"poll_seconds": 1}},
        )
        assert response.status_code == 422

    async def test_unknown_driver_lists_the_valid_ones(self, admin: LoggedInClient) -> None:
        response = await admin.client.post(
            "/api/v1/sensors",
            headers=admin.headers(),
            json={"name": "x", "driver": "definitely-not-a-driver"},
        )
        assert response.status_code == 422
        assert "available_drivers" in response.json()["error"]["details"]

    async def test_lab_sensor_is_flagged_from_the_driver(self, admin: LoggedInClient) -> None:
        """The flag comes from the driver class, so a laboratory sensor cannot
        be configured to masquerade as a real one."""
        response = await admin.client.post(
            "/api/v1/sensors",
            headers=admin.headers(),
            json={
                "name": "lab",
                "driver": "lab_synthetic",
                "config": {"scenario": "port_scan"},
                # Even though the request says nothing about simulation.
            },
        )
        assert response.status_code == 201
        assert response.json()["is_simulation"] is True

    async def test_duplicate_name_conflicts(self, admin: LoggedInClient) -> None:
        payload = {"name": "arp", "driver": "arp_discovery"}
        assert (
            await admin.client.post("/api/v1/sensors", headers=admin.headers(), json=payload)
        ).status_code == 201
        assert (
            await admin.client.post("/api/v1/sensors", headers=admin.headers(), json=payload)
        ).status_code == 409

    async def test_update_requires_a_reason(self, admin: LoggedInClient) -> None:
        created = (
            await admin.client.post(
                "/api/v1/sensors",
                headers=admin.headers(),
                json={"name": "arp", "driver": "arp_discovery"},
            )
        ).json()

        without_reason = await admin.client.patch(
            f"/api/v1/sensors/{created['id']}",
            headers=admin.headers(),
            json={"enabled": False},
        )
        assert without_reason.status_code == 422

        with_reason = await admin.client.patch(
            f"/api/v1/sensors/{created['id']}",
            headers=admin.headers(),
            json={"enabled": False, "reason": "Taking it down for maintenance."},
        )
        assert with_reason.status_code == 200
        assert with_reason.json()["enabled"] is False

    async def test_deleting_a_sensor_keeps_its_events(
        self, admin: LoggedInClient, database: Database
    ) -> None:
        """Deleting a sensor must not erase the evidence it collected."""
        await seed_events(database, count=3, prefix="real")
        sensors = (await admin.client.get("/api/v1/sensors")).json()
        sensor_id = sensors[0]["id"]

        response = await admin.client.request(
            "DELETE",
            f"/api/v1/sensors/{sensor_id}",
            headers=admin.headers(),
            json={"reason": "No longer needed."},
        )
        assert response.status_code == 200

        events = (await admin.client.get("/api/v1/events")).json()
        assert len(events["items"]) == 3
        # The sensor's name survives on the events, because it was copied.
        assert events["items"][0]["sensor_name"] == "real-sensor"


class TestJobApi:
    async def test_queue_stats_lists_known_kinds(self, viewer: LoggedInClient) -> None:
        import nexus.workers.handlers  # noqa: F401

        response = await viewer.client.get("/api/v1/jobs/stats")
        assert response.status_code == 200
        body = response.json()
        assert "retention.prune_events" in body["known_kinds"]
        assert body["healthy"] is True

    async def test_viewer_cannot_cancel(self, viewer: LoggedInClient, database: Database) -> None:
        from nexus.services.jobs import JobQueue

        async with database.session() as session:
            job = await JobQueue(session).enqueue("test.work")
            assert job is not None
            job_id = job.id

        response = await viewer.client.post(
            f"/api/v1/jobs/{job_id}/cancel", headers=viewer.headers()
        )
        assert response.status_code == 403

    async def test_operator_can_cancel_and_retry(
        self, operator: LoggedInClient, database: Database
    ) -> None:
        from nexus.services.jobs import JobQueue

        async with database.session() as session:
            job = await JobQueue(session).enqueue("test.work")
            assert job is not None
            job_id = job.id

        cancelled = await operator.client.post(
            f"/api/v1/jobs/{job_id}/cancel", headers=operator.headers()
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"

        retried = await operator.client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=operator.headers()
        )
        assert retried.status_code == 200
        assert retried.json()["status"] == "PENDING"
        assert retried.json()["attempts"] == 0

    async def test_retrying_a_running_job_conflicts(
        self, operator: LoggedInClient, database: Database
    ) -> None:
        """Retrying a running job would run it twice concurrently."""
        from nexus.services.jobs import JobQueue

        async with database.session() as session:
            queue = JobQueue(session)
            job = await queue.enqueue("test.work")
            assert job is not None
            job_id = job.id
        async with database.session() as session:
            await JobQueue(session).claim(worker_id="someone")

        response = await operator.client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=operator.headers()
        )
        assert response.status_code == 409
