"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created: ${create_date}

Every migration must define a working ``downgrade``. A migration you cannot
reverse is a deployment you cannot roll back.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
