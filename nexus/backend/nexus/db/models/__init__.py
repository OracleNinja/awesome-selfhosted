"""ORM models.

Importing this package imports every model module, which is what registers the
tables on ``Base.metadata``. Alembic's autogenerate compares that metadata
against the live database, so a model that is never imported is a table that
autogenerate will cheerfully propose to *drop*. Adding a model means adding it
here.
"""

from nexus.db.models.audit import GENESIS_HASH, AuditEvent
from nexus.db.models.device import DEVICE_STATES, RISK_LEVELS, Device
from nexus.db.models.event import EVENT_CATEGORIES, SEVERITIES, SecurityEvent
from nexus.db.models.finding import (
    ACTIVE_FINDING_STATUSES,
    DETECTION_TYPES,
    EVENT_STREAM,
    FINDING_STATUSES,
    OBSERVATION_ROLES,
    DetectionState,
    DeviceRiskAssessment,
    Finding,
    FindingObservation,
)
from nexus.db.models.job import JOB_STATUSES, TERMINAL_STATUSES, Job
from nexus.db.models.sensor import SENSOR_STATUSES, Sensor
from nexus.db.models.setting import AppSetting
from nexus.db.models.user import User, UserSession

__all__ = [
    "ACTIVE_FINDING_STATUSES",
    "DETECTION_TYPES",
    "DEVICE_STATES",
    "EVENT_CATEGORIES",
    "EVENT_STREAM",
    "FINDING_STATUSES",
    "GENESIS_HASH",
    "JOB_STATUSES",
    "OBSERVATION_ROLES",
    "RISK_LEVELS",
    "SENSOR_STATUSES",
    "SEVERITIES",
    "TERMINAL_STATUSES",
    "AppSetting",
    "AuditEvent",
    "DetectionState",
    "Device",
    "DeviceRiskAssessment",
    "Finding",
    "FindingObservation",
    "Job",
    "SecurityEvent",
    "Sensor",
    "User",
    "UserSession",
]
