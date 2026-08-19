"""Ingestion: turning raw observations into stored, normalised events.

This is the seam between "what a sensor saw" and "what the rest of the system
reasons about". Everything upstream is driver-specific; everything downstream —
detection, risk, the UI, exports — sees one shape.

Four jobs, in order:

1. **Deduplicate.** Each observation carries a deterministic id derived from
   its own content. A sensor restarting and re-reading the same kernel table
   produces the same ids, and the unique index discards them. Without this,
   every restart re-imports history and inflates every count, every rate, and
   every detection threshold.
2. **Resolve the device.** An observation mentions a MAC or an IP; the system
   reasons about devices. Resolution is an upsert, because two workers can
   ingest observations about the same device at the same moment.
3. **Normalise.** Map driver-specific fields onto the common columns, clamp
   everything to its declared bounds, and drop anything malformed rather than
   storing it and hoping.
4. **Stamp provenance.** Which sensor, which driver, and — critically —
   whether this is simulated. The simulation flag is taken from the *driver
   class*, never from the observation, so a laboratory sensor cannot produce
   data that looks real even if a caller passes the wrong argument.

Batching
--------
Events arrive in bursts (a capture window flushes 200 flows at once). Inserting
them one statement at a time means 200 round trips; a single multi-row `INSERT
… ON CONFLICT DO NOTHING` means one. At a few thousand events a minute that is
the difference between negligible and noticeable database load.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.core.logging import get_logger
from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.event import EVENT_CATEGORIES, SEVERITIES, SecurityEvent
from nexus.db.models.sensor import Sensor as SensorRow
from nexus.sensors.base import RawObservation

logger = get_logger(__name__)

_MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.IGNORECASE)

# A device is considered idle rather than active after this long unseen. Only a
# presentation concern — the underlying evidence is the last event's timestamp.
DEVICE_IDLE_AFTER_HOURS = 24


@dataclass
class IngestResult:
    """What one ingestion call actually did.

    Duplicates are reported rather than hidden: a sensor whose events are 90%
    duplicates has a bug in its id derivation, and that is invisible unless the
    number is surfaced.
    """

    stored: int = 0
    duplicates: int = 0
    rejected: int = 0
    devices_created: int = 0
    devices_updated: int = 0
    event_ids: list[int] = field(default_factory=list)
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def merge(self, other: IngestResult) -> IngestResult:
        self.stored += other.stored
        self.duplicates += other.duplicates
        self.rejected += other.rejected
        self.devices_created += other.devices_created
        self.devices_updated += other.devices_updated
        self.event_ids.extend(other.event_ids)
        for reason, count in other.rejection_reasons.items():
            self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + count
        return self


class IngestionService:
    """Normalises and stores observations from one sensor."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ingest(
        self, observations: list[RawObservation], sensor: SensorRow, *, driver_is_simulation: bool
    ) -> IngestResult:
        """Store a batch. Idempotent: re-ingesting the same batch stores nothing.

        ``driver_is_simulation`` comes from the driver class rather than from
        the sensor row or the observation, so the honesty flag cannot be
        forgotten or overridden by data.
        """
        result = IngestResult()
        if not observations:
            return result

        rows: list[dict[str, Any]] = []
        for observation in observations:
            try:
                normalised = self._normalise(observation, sensor, driver_is_simulation)
            except _RejectedObservation as exc:
                result.rejected += 1
                result.rejection_reasons[exc.reason] = (
                    result.rejection_reasons.get(exc.reason, 0) + 1
                )
                continue
            rows.append(normalised)

        if not rows:
            return result

        # Devices first: events reference them, and resolving in one pass lets
        # a batch about one device do a single upsert rather than N.
        device_map = await self._resolve_devices(rows, result, is_simulation=driver_is_simulation)
        for row in rows:
            key = _device_key(row)
            if key is not None:
                row["device_id"] = device_map.get(key)

        # One statement for the whole batch. DO NOTHING on the event_uid index
        # makes re-delivery free, and RETURNING tells us which rows were
        # actually new — the difference between stored and duplicate.
        statement = (
            pg_insert(SecurityEvent)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["event_uid"])
            .returning(SecurityEvent.id)
        )
        inserted = list((await self._session.execute(statement)).scalars().all())

        result.stored = len(inserted)
        result.duplicates = len(rows) - len(inserted)
        result.event_ids = inserted

        if result.stored:
            sensor.events_ingested += result.stored
            sensor.last_event_at = utcnow()
        if result.rejected:
            sensor.events_dropped += result.rejected

        logger.debug(
            "events_ingested",
            extra={
                "sensor": sensor.name,
                "stored": result.stored,
                "duplicates": result.duplicates,
                "rejected": result.rejected,
            },
        )
        return result

    # --------------------------------------------------------- normalisation --

    def _normalise(
        self, observation: RawObservation, sensor: SensorRow, is_simulation: bool
    ) -> dict[str, Any]:
        """Map one observation onto the event columns.

        Raises :class:`_RejectedObservation` rather than storing something
        malformed. A rejected observation is counted and reported; silently
        coercing bad input into a plausible-looking row is how a security tool
        ends up lying.
        """
        observation.truncated()

        if observation.category not in EVENT_CATEGORIES:
            raise _RejectedObservation("unknown_category")
        severity = observation.severity if observation.severity in SEVERITIES else "INFO"
        if not observation.kind or not observation.summary:
            raise _RejectedObservation("missing_kind_or_summary")

        mac = _normalise_mac(observation.mac_address)
        if observation.mac_address and mac is None:
            raise _RejectedObservation("invalid_mac")

        return {
            "event_uid": observation.event_uid(sensor.name),
            "occurred_at": observation.occurred_at,
            "received_at": utcnow(),
            "sensor_id": sensor.id,
            "sensor_name": sensor.name[:64],
            "driver": sensor.driver[:32],
            "category": observation.category,
            "kind": observation.kind[:64],
            "severity": severity,
            "confidence": max(0, min(100, int(observation.confidence))),
            "source_ip": observation.source_ip,
            "destination_ip": observation.destination_ip,
            "source_port": _clamp_port(observation.source_port),
            "destination_port": _clamp_port(observation.destination_port),
            "protocol": (observation.protocol or None) and observation.protocol[:16],
            "mac_address": mac,
            "summary": observation.summary,
            "attributes": _json_safe(observation.attributes),
            "raw": observation.raw,
            # From the driver class, not from the observation or the row.
            "is_simulation": is_simulation,
            "_hostname": observation.hostname,  # consumed by device resolution
        }

    # ----------------------------------------------------- device resolution --

    async def _resolve_devices(
        self, rows: list[dict[str, Any]], result: IngestResult, *, is_simulation: bool
    ) -> dict[tuple, uuid.UUID]:
        """Upsert every device mentioned in the batch, returning key → id.

        Identity precedence is MAC first, then IP. A MAC survives a DHCP lease
        change, so keying on it keeps one device as one device; keying on IP
        would make every lease renewal look like a new host.

        The upsert is `ON CONFLICT DO UPDATE`, not select-then-insert: two
        workers ingesting observations about the same device concurrently would
        otherwise both see "not present" and both insert.
        """
        keys: dict[tuple, dict[str, Any]] = {}
        for row in rows:
            key = _device_key(row)
            if key is None:
                continue
            # Later observations in a batch win, so the stored address is the
            # most recent one seen.
            keys[key] = {
                "mac_address": row["mac_address"],
                "ip_address": row["source_ip"],
                "hostname": row.pop("_hostname", None) or None,
                "occurred_at": row["occurred_at"],
            }
        for row in rows:
            row.pop("_hostname", None)

        if not keys:
            return {}

        resolved: dict[tuple, uuid.UUID] = {}
        for key, data in keys.items():
            device_id, created = await self._upsert_device(data, is_simulation=is_simulation)
            resolved[key] = device_id
            if created:
                result.devices_created += 1
            else:
                result.devices_updated += 1
        return resolved

    async def _upsert_device(
        self, data: dict[str, Any], *, is_simulation: bool
    ) -> tuple[uuid.UUID, bool]:
        now = utcnow()
        mac = data["mac_address"]
        ip = data["ip_address"]

        values = {
            "id": uuid.uuid4(),
            "mac_address": mac,
            "ip_address": ip,
            "hostname": data["hostname"],
            "first_seen_at": now,
            "last_seen_at": now,
            "state": "ACTIVE",
            "is_simulation": is_simulation,
        }

        update_values = {
            "last_seen_at": now,
            # COALESCE order matters: keep what we already know when the new
            # observation is silent about it. A flow event with no hostname
            # must not erase a hostname learned from DHCP.
            "ip_address": _coalesce_new(Device.ip_address, ip),
            "hostname": _coalesce_new(Device.hostname, data["hostname"]),
            # A device that was IDLE and is seen again is ACTIVE — but a
            # QUARANTINED device stays quarantined. Ingestion must never
            # silently undo a response action.
            "state": _reactivate_state(),
        }

        # Which unique index the upsert targets depends on how the device is
        # identified: the MAC unique constraint, or the partial unique index on
        # IP that covers devices with no visible MAC.
        insert_statement = pg_insert(Device).values(**values)
        if mac:
            statement = insert_statement.on_conflict_do_update(
                index_elements=["mac_address"], set_=update_values
            )
        else:
            statement = insert_statement.on_conflict_do_update(
                index_elements=["ip_address"],
                index_where=Device.mac_address.is_(None),
                set_=update_values,
            )
        returning_statement = statement.returning(Device.id, Device.created_at)

        row = (await self._session.execute(returning_statement)).one()
        device_id, created_at = row
        # created_at within a second of now means this insert created it. Good
        # enough for a counter; nothing depends on it being exact.
        created = (now - created_at).total_seconds() < 1
        return device_id, created


class _RejectedObservation(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _device_key(row: dict[str, Any]) -> tuple | None:
    if row.get("mac_address"):
        return ("mac", row["mac_address"])
    if row.get("source_ip"):
        return ("ip", row["source_ip"])
    return None


def _normalise_mac(value: str | None) -> str | None:
    """Lowercase, colon-separated, or ``None``.

    One canonical form at one place, so ``AA-BB-CC-DD-EE-FF`` from one sensor
    and ``aa:bb:cc:dd:ee:ff`` from another are the same device rather than two.
    """
    if not value:
        return None
    candidate = value.strip().lower().replace("-", ":")
    if not _MAC_RE.match(candidate):
        return None
    return candidate


def _clamp_port(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 <= port <= 65535 else None


def _json_safe(attributes: dict[str, Any]) -> dict[str, Any]:
    """Ensure the attributes blob can round-trip through JSONB.

    Sensor authors will eventually put a ``datetime`` or a ``set`` in here. The
    insert would fail, and one bad attribute would drop a whole batch of
    otherwise-good events, so unknown types are stringified instead.
    """
    safe: dict[str, Any] = {}
    for key, value in list(attributes.items())[:100]:
        key_str = str(key)[:64]
        if value is None or isinstance(value, (str, int, float, bool)):
            safe[key_str] = value
        elif isinstance(value, (list, tuple, set)):
            safe[key_str] = [
                str(item) if not isinstance(item, (str, int, float, bool)) else item
                for item in list(value)[:100]
            ]
        elif isinstance(value, dict):
            safe[key_str] = {str(k)[:64]: str(v) for k, v in list(value.items())[:50]}
        else:
            safe[key_str] = str(value)[:500]
    return safe


def _coalesce_new(column, new_value):
    """Prefer the new value, fall back to the stored one.

    Expressed as SQL so it happens inside the upsert rather than requiring a
    read-modify-write round trip that another worker could interleave with.
    """
    from sqlalchemy import func, literal

    if new_value is None:
        return column
    # The literal must carry the column's own type. Without it PostgreSQL sees
    # COALESCE(varchar, inet) and refuses — a text literal is not an address,
    # and the database is right to say so.
    return func.coalesce(literal(new_value, type_=column.type), column)


def _reactivate_state():
    """Mark a device ACTIVE unless it is quarantined.

    Ingestion must never undo a response action. A quarantined device is still
    observed — that is the point — but seeing it does not release it.
    """
    from sqlalchemy import case

    return case((Device.state == "QUARANTINED", "QUARANTINED"), else_="ACTIVE")


async def find_device(session: AsyncSession, *, mac: str | None, ip: str | None) -> Device | None:
    """Look up a device the same way ingestion resolves one."""
    if mac:
        normalised = _normalise_mac(mac)
        if normalised:
            found = await session.execute(select(Device).where(Device.mac_address == normalised))
            device = found.scalar_one_or_none()
            if device is not None:
                return device
    if ip:
        found = await session.execute(
            select(Device).where(Device.ip_address == ip, Device.mac_address.is_(None))
        )
        return found.scalar_one_or_none()
    return None
