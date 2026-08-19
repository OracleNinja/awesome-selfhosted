"""Schemas for events, devices, sensors, and jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexus.api.schemas.common import IPAddressStr


class EventSummary(BaseModel):
    """One event as it appears in a list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    received_at: datetime
    category: str
    kind: str
    severity: str
    confidence: int
    summary: str
    sensor_name: str
    driver: str
    device_id: uuid.UUID | None = None
    source_ip: IPAddressStr = None
    destination_ip: IPAddressStr = None
    destination_port: int | None = None
    protocol: str | None = None
    mac_address: str | None = None
    is_simulation: bool = Field(
        description=(
            "True for laboratory data. Real-network views exclude it by "
            "default; a client must never present simulated events as observed."
        )
    )


class EventDetail(EventSummary):
    """One event with its full normalised detail and the original observation."""

    source_port: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw: str | None = Field(
        default=None,
        description=(
            "The observation as received, truncated. This is the evidence: if "
            "normalisation was wrong, this is what was actually seen."
        ),
    )


class EventPage(BaseModel):
    items: list[EventSummary]
    next_cursor: int | None = Field(
        default=None, description="Pass as `before_id` for the next (older) page."
    )
    has_more: bool


class DeviceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mac_address: str | None = None
    ip_address: IPAddressStr = None
    hostname: str | None = None
    vendor: str | None = Field(
        default=None,
        description=(
            "Null means no OUI database is loaded — not 'unknown manufacturer'. "
            "Clients should render the two states differently."
        ),
    )
    label: str | None = None
    tags: list[Any] = Field(default_factory=list)
    is_trusted: bool
    state: str
    risk_score: int | None = Field(
        default=None,
        description="Null until the risk engine has assessed this device. Not the same as zero.",
    )
    risk_level: str
    risk_updated_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    is_simulation: bool


class DeviceDetail(DeviceSummary):
    notes: str | None = None
    recent_events: list[EventSummary] = Field(default_factory=list)
    event_count_24h: int = 0


class DevicePage(BaseModel):
    items: list[DeviceSummary]
    total: int
    has_more: bool


class UpdateDeviceRequest(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=32)
    is_trusted: bool | None = Field(
        default=None,
        description=(
            "Suppresses risk escalation for this device. It does not suppress "
            "recording: a trusted device that starts scanning still produces events."
        ),
    )


class SensorSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    driver: str
    description: str | None = None
    enabled: bool
    status: str = Field(
        description=(
            "STOPPED | STARTING | RUNNING | COMPLETED | DEGRADED | FAILED | "
            "NOT_AVAILABLE | NOT_CONFIGURED.\n\n"
            "Only DEGRADED and FAILED are faults. COMPLETED means a finite "
            "source (a laboratory scenario) finished its run; NOT_AVAILABLE "
            "means this host cannot support the driver; NOT_CONFIGURED means it "
            "has not been set up."
        )
    )
    status_detail: str | None = Field(
        default=None, description="Why it is in that state, and what to do about it."
    )
    config: dict[str, Any] = Field(default_factory=dict)
    last_heartbeat_at: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    events_ingested: int
    events_dropped: int
    is_simulation: bool = Field(
        description="True for laboratory sensors. Everything they produce is flagged simulated."
    )


class SensorDriverInfo(BaseModel):
    driver: str
    description: str
    produces_simulated_data: bool
    requires_privileges: bool
    availability: str = Field(description="AVAILABLE | NOT_AVAILABLE | NOT_CONFIGURED")
    availability_detail: str
    remedy: str | None = Field(
        default=None, description="What an operator would have to do to make it available."
    )


class CreateSensorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    driver: str = Field(max_length=32)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateSensorRequest(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    reason: str = Field(
        min_length=3,
        max_length=500,
        description="Recorded in the audit log. Changing what the platform watches is a security-relevant act.",
    )


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    queue: str
    kind: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    run_after: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    timeout_seconds: int
    last_error: str | None = None
    result: dict[str, Any] | None = None
    cancel_requested: bool
    created_at: datetime


class JobPage(BaseModel):
    items: list[JobSummary]
    total: int


class QueueStatsResponse(BaseModel):
    pending: int
    running: int
    dead: int
    oldest_pending_age_seconds: float | None
    healthy: bool
    known_kinds: dict[str, str] = Field(
        description="Job kinds this build can run, with descriptions."
    )
