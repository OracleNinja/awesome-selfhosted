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
from nexus.db.models.job import JOB_STATUSES, TERMINAL_STATUSES, Job
from nexus.db.models.sensor import SENSOR_STATUSES, Sensor
from nexus.db.models.setting import AppSetting
from nexus.db.models.user import User, UserSession

__all__ = [
    "DEVICE_STATES",
    "EVENT_CATEGORIES",
    "GENESIS_HASH",
    "JOB_STATUSES",
    "RISK_LEVELS",
    "SENSOR_STATUSES",
    "SEVERITIES",
    "TERMINAL_STATUSES",
    "AppSetting",
    "AuditEvent",
    "Device",
    "Job",
    "SecurityEvent",
    "Sensor",
    "User",
    "UserSession",
]
