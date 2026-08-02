"""Index the foreign keys that joins and cascades actually use

PostgreSQL indexes the *referenced* side of a foreign key automatically but not
the *referencing* side. Every order serialisation loads its items and events,
every customer page loads their orders and designs, and every delete cascades —
all of which were sequential scans.

Revision ID: b1c2d3e4f5a6
Revises: aa880380da61
Create Date: 2026-08-02
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "aa880380da61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index name, table, column)
INDEXES = [
    ("ix_order_items_order_id", "order_items", "order_id"),
    ("ix_order_items_design_id", "order_items", "design_id"),
    ("ix_order_items_product_id", "order_items", "product_id"),
    ("ix_order_events_order_id", "order_events", "order_id"),
    ("ix_orders_customer_id", "orders", "customer_id"),
    ("ix_orders_assigned_machine_id", "orders", "assigned_machine_id"),
    ("ix_invoices_order_id", "invoices", "order_id"),
    ("ix_invoices_customer_id", "invoices", "customer_id"),
    ("ix_designs_customer_id", "designs", "customer_id"),
    ("ix_designs_owner_id", "designs", "owner_id"),
    ("ix_machines_organization_id", "machines", "organization_id"),
    ("ix_memberships_organization_id", "memberships", "organization_id"),
    ("ix_embroidery_settings_machine_id", "embroidery_settings", "machine_id"),
    ("ix_usage_events_user_id", "usage_events", "user_id"),
    ("ix_order_events_user_id", "order_events", "user_id"),
]


def upgrade() -> None:
    for name, table, column in INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in reversed(INDEXES):
        op.drop_index(name, table_name=table)
