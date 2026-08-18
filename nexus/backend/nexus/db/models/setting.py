"""Runtime-tunable settings.

Configuration in :mod:`nexus.core.config` is what the process needs in order to
*boot*: the database URL, the secret key, which networks are owned. It comes
from the environment and never changes while the process runs.

This table holds the other kind: values an operator adjusts from the UI while
the system is running — a detection threshold, a retention override. They are
data, so they live in the database, are versioned by the audit log, and take
effect without a restart.

Why not put everything here? Because a setting that must be correct before the
database is reachable cannot live in the database, and because environment
configuration is reviewable in a deployment manifest, whereas a database row is
not.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus.db.base import Base, TimestampMixin


class AppSetting(Base, TimestampMixin):
    """A single namespaced setting.

    The key is the primary key: settings are looked up by name, there is
    exactly one row per name, and a surrogate id would add an index without
    adding meaning.
    """

    __tablename__ = "app_settings"

    # Dotted namespace, e.g. "retention.events_days", "quarantine.backend".
    key: Mapped[str] = mapped_column(String(128), primary_key=True)

    # JSONB so a setting can hold a scalar, a list, or a small object without a
    # schema change. Values are validated against a registry in the service
    # layer before they are written — "it is JSON" is not validation.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Optimistic-concurrency counter: two admins editing the same setting from
    # two tabs must not silently overwrite each other. The API rejects a write
    # carrying a stale version with 409 CONFLICT.
    version: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AppSetting {self.key}>"
