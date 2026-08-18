"""Foundation schema: identity, sessions, audit log, runtime settings.

Revision ID: 0001_foundation
Revises:
Created: 2026-08-18

Revision ids here are readable rather than random hex. Alembic only requires
uniqueness, and ``0001_foundation`` tells a reviewer what the migration is
during a rollback at 3am, where ``86e1556b687d`` does not.

This migration establishes the four tables everything else depends on:

    users           who may log in, and as what role
    user_sessions   live logins, revocable server-side
    audit_events    hash-chained record of sensitive actions
    app_settings    operator-tunable values that change without a deploy
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "password_algorithm",
            sa.String(length=32),
            server_default="argon2id",
            nullable=False,
        ),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The role vocabulary is enforced by the database as well as by the
        # Python enum, so a maintenance script writing raw SQL cannot create an
        # account with a role the permission matrix does not recognise.
        sa.CheckConstraint(
            "role IN ('ADMIN', 'OPERATOR', 'VIEWER')", name=op.f("ck_users_role_valid")
        ),
        sa.CheckConstraint(
            "char_length(username) >= 3", name=op.f("ck_users_username_length")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        # CASCADE: deleting a user must not leave sessions that authenticate
        # nobody. Deactivation, not deletion, is the normal path for offboarding.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
    )
    op.create_index(
        "ix_user_sessions_expires_at", "user_sessions", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_user_sessions_user_id_expires_at",
        "user_sessions",
        ["user_id", "expires_at"],
        unique=False,
    )

    op.create_table(
        "audit_events",
        # GENERATED ALWAYS AS IDENTITY: the sequence is the only source of ids.
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("target_label", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("source_ip", postgresql.INET(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('SUCCESS', 'FAILURE', 'DENIED')",
            name=op.f("ck_audit_events_outcome_valid"),
        ),
        # SET NULL, not CASCADE: history survives the deletion of its actor.
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        # Unique entry hashes: a duplicate would mean either a hash collision or
        # a replayed row, and both should fail loudly at insert time.
        sa.UniqueConstraint("entry_hash", name=op.f("uq_audit_events_entry_hash")),
    )
    # DESC indexes: the audit views all read newest-first, and a DESC index lets
    # PostgreSQL walk the index backwards without a sort node.
    op.create_index(
        "ix_audit_events_occurred_at",
        "audit_events",
        [sa.literal_column("occurred_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_actor_user_id_occurred_at",
        "audit_events",
        ["actor_user_id", sa.literal_column("occurred_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_action_occurred_at",
        "audit_events",
        ["action", sa.literal_column("occurred_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_target",
        "audit_events",
        ["target_type", "target_id"],
        unique=False,
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_app_settings_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    # Reverse creation order so foreign keys never block a drop.
    op.drop_table("app_settings")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_index("ix_audit_events_action_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_user_sessions_user_id_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("users")
