"""ORM models.

Importing this package imports every model module, which is what registers the
tables on ``Base.metadata``. Alembic's autogenerate compares that metadata
against the live database, so a model that is never imported is a table that
autogenerate will cheerfully propose to *drop*. Adding a model means adding it
here.
"""

from nexus.db.models.audit import GENESIS_HASH, AuditEvent
from nexus.db.models.setting import AppSetting
from nexus.db.models.user import User, UserSession

__all__ = [
    "GENESIS_HASH",
    "AppSetting",
    "AuditEvent",
    "User",
    "UserSession",
]
