"""SQLAlchemy models.

Kept in one module so the relationship graph is readable in one screen.  The
schema is multi-tenant from day one: every business record hangs off an
``organization`` so team accounts and enterprise customers do not need a
migration later.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


# --------------------------------------------------------------------- enums
class PlanTier(str, enum.Enum):
    free = "free"
    pro = "pro"
    business = "business"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    trialing = "trialing"
    past_due = "past_due"
    canceled = "canceled"


class OrgRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    digitizer = "digitizer"
    operator = "operator"
    viewer = "viewer"


class DesignStatus(str, enum.Enum):
    draft = "draft"
    analyzing = "analyzing"
    vectorized = "vectorized"
    digitizing = "digitizing"
    ready = "ready"
    failed = "failed"
    archived = "archived"


class DesignSource(str, enum.Enum):
    upload_image = "upload_image"
    upload_svg = "upload_svg"
    upload_logo = "upload_logo"
    ai_generated = "ai_generated"
    text = "text"


class FileKind(str, enum.Enum):
    original = "original"
    thumbnail = "thumbnail"
    vector = "vector"
    preview = "preview"
    stitch = "stitch"
    package = "package"
    document = "document"
    mockup = "mockup"


class OrderStatus(str, enum.Enum):
    quote = "quote"
    approved = "approved"
    digitizing = "digitizing"
    sewing = "sewing"
    completed = "completed"
    delivered = "delivered"
    cancelled = "cancelled"


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    paid = "paid"
    overdue = "overdue"
    void = "void"


# ------------------------------------------------------------- organisations
class Organization(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    members: Mapped[list["Membership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="organization", uselist=False, cascade="all, delete-orphan"
    )


class User(UUIDMixin, TimestampMixin, Base):
    """Mirror of the Supabase auth user.

    ``id`` is the Supabase ``auth.users.id`` so no mapping table is needed.
    Passwords and sessions live in Supabase; this row holds product state.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(160))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    locale: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    designs: Mapped[list["Design"]] = relationship(back_populates="owner")


class Membership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_membership"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, name="org_role"), default=OrgRole.owner, nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="members")


class Subscription(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    tier: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, name="plan_tier"), default=PlanTier.free, nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.active, nullable=False,
    )
    seats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_customer_id: Mapped[str | None] = mapped_column(String(120))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="subscription")


class UsageEvent(UUIDMixin, Base):
    """Append-only meter. Quota checks count rows in the current period, which
    keeps the counter honest even if a subscription changes mid-month."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_org_period", "organization_id", "occurred_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)   # design | export | ai_analysis
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# -------------------------------------------------------------------- design
class Design(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "designs"
    __table_args__ = (Index("ix_designs_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DesignStatus] = mapped_column(
        Enum(DesignStatus, name="design_status"), default=DesignStatus.draft, nullable=False
    )
    source: Mapped[DesignSource] = mapped_column(
        Enum(DesignSource, name="design_source"), default=DesignSource.upload_image, nullable=False
    )
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Analyzer output (Module 2).
    analysis: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    compatibility_score: Mapped[int | None] = mapped_column(Integer)

    # Digitizer output (Module 4).
    stitch_count: Mapped[int | None] = mapped_column(Integer)
    color_count: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[float | None] = mapped_column(Float)
    height_mm: Mapped[float | None] = mapped_column(Float)
    trim_count: Mapped[int | None] = mapped_column(Integer)
    thread_length_m: Mapped[float | None] = mapped_column(Float)
    estimated_minutes: Mapped[float | None] = mapped_column(Float)
    thread_chart: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    issues: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Cached stitch stream for the studio; large designs are decimated.
    stitch_preview: Mapped[dict | None] = mapped_column(JSONB)

    owner: Mapped[User | None] = relationship(back_populates="designs")
    files: Mapped[list["DesignFile"]] = relationship(
        back_populates="design", cascade="all, delete-orphan"
    )
    settings: Mapped["EmbroiderySettings | None"] = relationship(
        back_populates="design", uselist=False, cascade="all, delete-orphan"
    )
    customer: Mapped["Customer | None"] = relationship(back_populates="designs")


class DesignFile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "design_files"
    __table_args__ = (Index("ix_files_design_kind", "design_id", "kind"),)

    design_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("designs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[FileKind] = mapped_column(Enum(FileKind, name="file_kind"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    design: Mapped[Design] = relationship(back_populates="files")


class EmbroiderySettings(UUIDMixin, TimestampMixin, Base):
    """The digitizing parameters a design was produced with — kept so a design
    can be re-digitized reproducibly after an engine upgrade."""

    __tablename__ = "embroidery_settings"

    design_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("designs.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    fabric: Mapped[str] = mapped_column(String(40), default="cotton_shirt", nullable=False)
    thread_catalog: Mapped[str] = mapped_column(String(40), default="genie_standard", nullable=False)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("machines.id", ondelete="SET NULL")
    )
    target_width_mm: Mapped[float | None] = mapped_column(Float)
    target_height_mm: Mapped[float | None] = mapped_column(Float)
    fill_angle_deg: Mapped[float] = mapped_column(Float, default=45.0, nullable=False)
    density_scale: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    auto_underlay: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_satin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_colors: Mapped[int | None] = mapped_column(Integer)
    engine_version: Mapped[str] = mapped_column(String(20), default="1.0", nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    design: Mapped[Design] = relationship(back_populates="settings")
    machine: Mapped["Machine | None"] = relationship()


# ------------------------------------------------------------------ machines
class Machine(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "machines"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    heads: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    needle_count: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    hoop_width_mm: Mapped[float] = mapped_column(Float, default=360.0, nullable=False)
    hoop_height_mm: Mapped[float] = mapped_column(Float, default=200.0, nullable=False)
    max_stitch_count: Mapped[int] = mapped_column(Integer, default=200_000, nullable=False)
    max_speed_spm: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    supported_formats: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    hourly_rate: Mapped[float | None] = mapped_column(Float)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # Reserved for direct machine connection (Module: future ready).
    connection: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# ----------------------------------------------------------------- customers
class Customer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_org_name", "organization_id", "name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    company: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    tax_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    designs: Mapped[list[Design]] = relationship(back_populates="customer")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


# ------------------------------------------------------------------ products
class Product(UUIDMixin, TimestampMixin, Base):
    """A blank garment or finished SKU held in inventory."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "sku", name="uq_product_sku"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(60))
    fabric_profile: Mapped[str] = mapped_column(String(40), default="cotton_shirt", nullable=False)
    color: Mapped[str | None] = mapped_column(String(60))
    size: Mapped[str | None] = mapped_column(String(20))
    blank_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reorder_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    supplier: Mapped[str | None] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# -------------------------------------------------------------------- orders
class Order(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_order_number"),
        Index("ix_orders_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.quote, nullable=False
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    rush: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    placement: Mapped[str | None] = mapped_column(String(80))
    assigned_machine_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("machines.id", ondelete="SET NULL")
    )

    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    discount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    total: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    cost_total: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)

    customer: Mapped[Customer | None] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan",
        order_by="OrderEvent.created_at",
    )

    @property
    def profit(self) -> float:
        return float(self.total or 0) - float(self.cost_total or 0)


class OrderItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_order_item_qty"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    design_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("designs.id", ondelete="SET NULL")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    placement: Mapped[str | None] = mapped_column(String(80))
    pricing_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    design: Mapped[Design | None] = relationship()
    product: Mapped[Product | None] = relationship()

    @property
    def line_total(self) -> float:
        return float(self.unit_price or 0) * int(self.quantity or 0)


class OrderEvent(UUIDMixin, Base):
    """Status history — the audit trail a production floor argues over."""

    __tablename__ = "order_events"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="events")


class Invoice(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_invoice_number"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"), default=InvoiceStatus.draft, nullable=False
    )
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    total: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    amount_paid: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    line_items: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    order: Mapped[Order | None] = relationship(back_populates="invoices")
    customer: Mapped[Customer | None] = relationship()

    @property
    def balance_due(self) -> float:
        return float(self.total or 0) - float(self.amount_paid or 0)


__all__ = [
    "Base",
    "Customer",
    "Design",
    "DesignFile",
    "DesignSource",
    "DesignStatus",
    "EmbroiderySettings",
    "FileKind",
    "Invoice",
    "InvoiceStatus",
    "Machine",
    "Membership",
    "Order",
    "OrderEvent",
    "OrderItem",
    "OrderStatus",
    "OrgRole",
    "Organization",
    "PlanTier",
    "Product",
    "Subscription",
    "SubscriptionStatus",
    "UsageEvent",
    "User",
]
