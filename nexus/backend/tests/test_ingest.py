"""Ingestion tests: deduplication, device identity, and the simulation flag."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from nexus.db.models.device import Device
from nexus.db.models.event import SecurityEvent
from nexus.db.models.sensor import Sensor as SensorRow
from nexus.db.session import Database
from nexus.sensors.base import RawObservation
from nexus.services.ingest import IngestionService

pytestmark = pytest.mark.integration


async def make_sensor(
    session, *, name: str = "test-sensor", driver: str = "arp_discovery", simulation: bool = False
) -> SensorRow:
    row = SensorRow(name=name, driver=driver, config={}, is_simulation=simulation)
    session.add(row)
    await session.flush()
    return row


def observation(**overrides) -> RawObservation:
    defaults = {
        "kind": "device.discovered",
        "category": "DISCOVERY",
        "summary": "Device aa:bb:cc:dd:ee:ff discovered at 192.168.1.10",
        "source_ip": "192.168.1.10",
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "uid_seed": "discovered|aa:bb:cc:dd:ee:ff",
    }
    return RawObservation(**{**defaults, **overrides})


class TestStorage:
    async def test_stores_a_normalised_event(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [observation()], sensor, driver_is_simulation=False
        )
        assert result.stored == 1

        event = (await session.execute(select(SecurityEvent))).scalar_one()
        assert event.kind == "device.discovered"
        assert event.category == "DISCOVERY"
        assert event.sensor_name == "test-sensor"
        assert event.driver == "arp_discovery"
        assert event.is_simulation is False
        assert event.device_id is not None

    async def test_batch_insert(self, session) -> None:
        sensor = await make_sensor(session)
        observations = [
            observation(uid_seed=f"seed-{index}", summary=f"event {index}") for index in range(50)
        ]
        result = await IngestionService(session).ingest(
            observations, sensor, driver_is_simulation=False
        )
        assert result.stored == 50

    async def test_counters_are_updated_on_the_sensor_row(self, session) -> None:
        sensor = await make_sensor(session)
        await IngestionService(session).ingest(
            [observation(uid_seed="a"), observation(uid_seed="b")],
            sensor,
            driver_is_simulation=False,
        )
        assert sensor.events_ingested == 2
        assert sensor.last_event_at is not None


class TestDeduplication:
    async def test_reingesting_the_same_observation_stores_nothing(self, session) -> None:
        """A sensor restarting re-reads the same kernel table. Without dedup,
        every restart re-imports history and inflates every count and every
        detection threshold downstream."""
        sensor = await make_sensor(session)
        service = IngestionService(session)

        first = await service.ingest([observation()], sensor, driver_is_simulation=False)
        second = await service.ingest([observation()], sensor, driver_is_simulation=False)

        assert first.stored == 1
        assert second.stored == 0
        assert second.duplicates == 1

        count = len((await session.execute(select(SecurityEvent))).scalars().all())
        assert count == 1

    async def test_duplicates_within_one_batch(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [observation(), observation(), observation(uid_seed="different")],
            sensor,
            driver_is_simulation=False,
        )
        # The multi-row insert collapses the identical pair via the unique index.
        assert result.stored == 2
        assert result.duplicates == 1

    async def test_same_observation_from_two_sensors_is_two_events(self, session) -> None:
        """Two sensors seeing the same fact are two pieces of evidence, and
        collapsing them would hide one of them going silent."""
        first_sensor = await make_sensor(session, name="sensor-one")
        second_sensor = await make_sensor(session, name="sensor-two")
        service = IngestionService(session)

        await service.ingest([observation()], first_sensor, driver_is_simulation=False)
        await service.ingest([observation()], second_sensor, driver_is_simulation=False)

        events = (await session.execute(select(SecurityEvent))).scalars().all()
        assert len(events) == 2


class TestDeviceResolution:
    async def test_creates_a_device(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [observation()], sensor, driver_is_simulation=False
        )
        assert result.devices_created == 1

        device = (await session.execute(select(Device))).scalar_one()
        assert device.mac_address == "aa:bb:cc:dd:ee:ff"
        assert str(device.ip_address) == "192.168.1.10"

    async def test_mac_survives_a_dhcp_lease_change(self, session) -> None:
        """Keying on the MAC keeps one device as one device. Keying on IP would
        make every lease renewal look like a new host."""
        sensor = await make_sensor(session)
        service = IngestionService(session)

        await service.ingest([observation()], sensor, driver_is_simulation=False)
        await service.ingest(
            [observation(source_ip="192.168.1.99", uid_seed="moved")],
            sensor,
            driver_is_simulation=False,
        )

        devices = (await session.execute(select(Device))).scalars().all()
        assert len(devices) == 1
        assert str(devices[0].ip_address) == "192.168.1.99"

    async def test_mac_formats_are_normalised_to_one_device(self, session) -> None:
        sensor = await make_sensor(session)
        service = IngestionService(session)

        await service.ingest([observation()], sensor, driver_is_simulation=False)
        await service.ingest(
            [observation(mac_address="AA-BB-CC-DD-EE-FF", uid_seed="upper")],
            sensor,
            driver_is_simulation=False,
        )

        devices = (await session.execute(select(Device))).scalars().all()
        assert len(devices) == 1

    async def test_device_without_a_mac_is_keyed_on_ip(self, session) -> None:
        sensor = await make_sensor(session)
        await IngestionService(session).ingest(
            [observation(mac_address=None, uid_seed="no-mac")], sensor, driver_is_simulation=False
        )
        device = (await session.execute(select(Device))).scalar_one()
        assert device.mac_address is None
        assert str(device.ip_address) == "192.168.1.10"

    async def test_hostname_is_not_erased_by_a_silent_observation(self, session) -> None:
        """A flow event that knows no hostname must not wipe one learned from
        DHCP."""
        sensor = await make_sensor(session)
        service = IngestionService(session)

        await service.ingest(
            [observation(hostname="kitchen-pi", uid_seed="with-name")],
            sensor,
            driver_is_simulation=False,
        )
        await service.ingest(
            [observation(hostname=None, uid_seed="without-name")],
            sensor,
            driver_is_simulation=False,
        )

        device = (await session.execute(select(Device))).scalar_one()
        assert device.hostname == "kitchen-pi"

    async def test_quarantine_survives_ingestion(self, session) -> None:
        """Ingestion must never undo a response action. Seeing a quarantined
        device is expected — that is the point — and does not release it."""
        sensor = await make_sensor(session)
        service = IngestionService(session)
        await service.ingest([observation()], sensor, driver_is_simulation=False)

        device = (await session.execute(select(Device))).scalar_one()
        device.state = "QUARANTINED"
        await session.flush()

        await service.ingest(
            [observation(uid_seed="seen-again")], sensor, driver_is_simulation=False
        )
        await session.refresh(device)
        assert device.state == "QUARANTINED"

    async def test_concurrent_ingestion_does_not_duplicate_a_device(
        self, database: Database
    ) -> None:
        """Two workers ingesting observations about the same device at the same
        moment would both see 'not present' without an upsert."""
        async with database.session() as setup:
            sensor = await make_sensor(setup)
            sensor_id = sensor.id

        async def ingest(index: int) -> None:
            async with database.session() as db_session:
                row = await db_session.get(SensorRow, sensor_id)
                await IngestionService(db_session).ingest(
                    [observation(uid_seed=f"concurrent-{index}")],
                    row,
                    driver_is_simulation=False,
                )

        await asyncio.gather(*(ingest(index) for index in range(8)))

        async with database.session() as db_session:
            devices = (await db_session.execute(select(Device))).scalars().all()
        assert len(devices) == 1


class TestSimulationFlag:
    async def test_lab_events_are_flagged_at_storage(self, session) -> None:
        """The flag comes from the driver class, so nothing downstream can
        mistake laboratory data for observation."""
        sensor = await make_sensor(session, driver="lab_synthetic", simulation=True)
        await IngestionService(session).ingest(
            [observation(summary="[SIMULATION] something")], sensor, driver_is_simulation=True
        )
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        assert event.is_simulation is True

    async def test_lab_devices_are_flagged_too(self, session) -> None:
        sensor = await make_sensor(session, driver="lab_synthetic", simulation=True)
        await IngestionService(session).ingest([observation()], sensor, driver_is_simulation=True)
        device = (await session.execute(select(Device))).scalar_one()
        assert device.is_simulation is True

    async def test_the_observation_cannot_override_the_driver(self, session) -> None:
        """Even if an observation claims otherwise, the driver decides."""
        sensor = await make_sensor(session, driver="lab_synthetic", simulation=True)
        await IngestionService(session).ingest(
            [observation(attributes={"simulation": False, "is_simulation": False})],
            sensor,
            driver_is_simulation=True,
        )
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        assert event.is_simulation is True


class TestValidation:
    async def test_unknown_category_is_rejected_and_counted(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [observation(category="MADE_UP")], sensor, driver_is_simulation=False
        )
        assert result.stored == 0
        assert result.rejected == 1
        assert result.rejection_reasons["unknown_category"] == 1

    async def test_invalid_mac_is_rejected(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [observation(mac_address="not-a-mac")], sensor, driver_is_simulation=False
        )
        assert result.rejected == 1

    async def test_unknown_severity_falls_back_to_info(self, session) -> None:
        sensor = await make_sensor(session)
        await IngestionService(session).ingest(
            [observation(severity="APOCALYPTIC")], sensor, driver_is_simulation=False
        )
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        assert event.severity == "INFO"

    async def test_out_of_range_values_are_clamped(self, session) -> None:
        sensor = await make_sensor(session)
        await IngestionService(session).ingest(
            [observation(confidence=500, source_port=99999, destination_port=-1)],
            sensor,
            driver_is_simulation=False,
        )
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        assert event.confidence == 100
        assert event.source_port is None
        assert event.destination_port is None

    async def test_unserialisable_attributes_do_not_drop_the_batch(self, session) -> None:
        """One sensor author putting a datetime in `attributes` must not take
        down a whole batch of otherwise-good events."""
        from datetime import datetime

        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [observation(attributes={"when": datetime.now(), "nested": {"a": object()}})],
            sensor,
            driver_is_simulation=False,
        )
        assert result.stored == 1

    async def test_oversized_strings_are_truncated(self, session) -> None:
        sensor = await make_sensor(session)
        await IngestionService(session).ingest(
            [observation(summary="x" * 5000, raw="y" * 100_000)],
            sensor,
            driver_is_simulation=False,
        )
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        assert len(event.summary) <= 500
        assert len(event.raw or "") <= 8192

    async def test_a_bad_observation_does_not_block_good_ones(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest(
            [
                observation(uid_seed="good-1"),
                observation(category="NONSENSE", uid_seed="bad"),
                observation(uid_seed="good-2"),
            ],
            sensor,
            driver_is_simulation=False,
        )
        assert result.stored == 2
        assert result.rejected == 1

    async def test_empty_batch_is_a_no_op(self, session) -> None:
        sensor = await make_sensor(session)
        result = await IngestionService(session).ingest([], sensor, driver_is_simulation=False)
        assert result.stored == 0
