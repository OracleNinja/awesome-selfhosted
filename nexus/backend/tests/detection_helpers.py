"""Shared helpers for the detection tests.

Observations are pushed through the real :class:`IngestionService` rather than
inserted directly, so every detection test also exercises normalisation, device
resolution and the simulation flag exactly as production does. A test that
hand-inserted rows could pass while the pipeline that feeds detection was
broken.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nexus.db.base import utcnow
from nexus.db.models.sensor import Sensor as SensorRow
from nexus.sensors.base import RawObservation
from nexus.services.ingest import IngestionService


async def make_sensor(
    session,
    *,
    name: str = "det-sensor",
    driver: str = "arp_discovery",
    simulation: bool = False,
) -> SensorRow:
    row = SensorRow(name=name, driver=driver, config={}, is_simulation=simulation)
    session.add(row)
    await session.flush()
    return row


async def ingest(
    session, sensor: SensorRow, observations: list[RawObservation], *, simulation=None
):
    """Store observations through the real ingestion path."""
    is_simulation = sensor.is_simulation if simulation is None else simulation
    return await IngestionService(session).ingest(
        observations, sensor, driver_is_simulation=is_simulation
    )


def discovery(
    *,
    mac: str = "aa:bb:cc:dd:ee:01",
    ip: str = "192.168.1.50",
    at: datetime | None = None,
    seed: str | None = None,
    hostname: str | None = None,
) -> RawObservation:
    moment = at or utcnow()
    return RawObservation(
        kind="device.discovered",
        category="DISCOVERY",
        summary=f"Device {mac} at {ip}",
        occurred_at=moment,
        source_ip=ip,
        mac_address=mac,
        hostname=hostname,
        uid_seed=seed or f"discovered|{mac}|{ip}|{moment.isoformat()}",
    )


def scan(
    *,
    ip: str = "192.168.1.50",
    mac: str = "aa:bb:cc:dd:ee:01",
    index: int = 0,
    at: datetime | None = None,
    severity: str = "MEDIUM",
    kind: str = "scan.port_probe",
) -> RawObservation:
    moment = at or utcnow()
    return RawObservation(
        kind=kind,
        category="SCAN",
        severity=severity,
        summary=f"Port probe {index} from {ip}",
        occurred_at=moment,
        source_ip=ip,
        mac_address=mac,
        destination_ip="192.168.1.1",
        destination_port=1000 + index,
        protocol="TCP",
        uid_seed=f"scan|{ip}|{index}|{moment.isoformat()}",
    )


def flow(
    *,
    source: str = "192.168.1.50",
    mac: str = "aa:bb:cc:dd:ee:01",
    destination: str = "203.0.113.10",
    port: int = 4444,
    protocol: str = "TCP",
    at: datetime | None = None,
    seed: str | None = None,
) -> RawObservation:
    moment = at or utcnow()
    return RawObservation(
        kind="flow.observed",
        category="TRAFFIC",
        summary=f"{source} -> {destination}:{port}",
        occurred_at=moment,
        source_ip=source,
        destination_ip=destination,
        destination_port=port,
        protocol=protocol,
        mac_address=mac,
        attributes={"bytes": 2048},
        uid_seed=seed or f"flow|{source}|{destination}|{port}|{moment.isoformat()}",
    )


def minutes_ago(count: int) -> datetime:
    return utcnow() - timedelta(minutes=count)
