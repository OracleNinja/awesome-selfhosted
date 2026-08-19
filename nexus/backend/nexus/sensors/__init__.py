"""Sensor drivers and the registry that resolves them.

Importing this package registers every driver. Like the job-handler registry,
this is an allow-list: a sensor row names a driver by string, and only strings
present here resolve to code. Importing a module path taken from the database
would turn any database write into arbitrary code execution.
"""

from __future__ import annotations

from nexus.core.errors import ValidationFailed
from nexus.sensors.arp_discovery import ArpDiscoverySensor
from nexus.sensors.base import (
    Availability,
    AvailabilityReport,
    RawObservation,
    Sensor,
)
from nexus.sensors.lab_synthetic import SCENARIOS, LabSyntheticSensor
from nexus.sensors.packet_capture import PacketCaptureSensor, list_interfaces
from nexus.sensors.syslog import SyslogSensor

# Driver name → class. Order is the order the "add sensor" screen offers them:
# the passive, no-privilege drivers first, simulation last.
SENSOR_DRIVERS: dict[str, type[Sensor]] = {
    ArpDiscoverySensor.driver: ArpDiscoverySensor,
    SyslogSensor.driver: SyslogSensor,
    PacketCaptureSensor.driver: PacketCaptureSensor,
    LabSyntheticSensor.driver: LabSyntheticSensor,
}


def resolve_driver(driver: str) -> type[Sensor]:
    """Look up a driver class by name.

    Raises :class:`ValidationFailed` for an unknown name, listing the valid
    ones — an operator mistyping a driver should be told what exists.
    """
    sensor_class = SENSOR_DRIVERS.get(driver)
    if sensor_class is None:
        raise ValidationFailed(
            f"Unknown sensor driver {driver!r}.",
            details={"available_drivers": sorted(SENSOR_DRIVERS)},
        )
    return sensor_class


def describe_drivers() -> list[dict]:
    """Catalogue for the sensors screen.

    Includes whether each driver produces simulated data and whether it needs
    elevated privileges, so an operator can see what they are enabling before
    they enable it.
    """
    return [
        {
            "driver": name,
            "description": sensor_class.description,
            "produces_simulated_data": sensor_class.produces_simulated_data,
            "requires_privileges": sensor_class.requires_privileges,
        }
        for name, sensor_class in SENSOR_DRIVERS.items()
    ]


__all__ = [
    "SCENARIOS",
    "SENSOR_DRIVERS",
    "ArpDiscoverySensor",
    "Availability",
    "AvailabilityReport",
    "LabSyntheticSensor",
    "PacketCaptureSensor",
    "RawObservation",
    "Sensor",
    "SyslogSensor",
    "describe_drivers",
    "list_interfaces",
    "resolve_driver",
]
