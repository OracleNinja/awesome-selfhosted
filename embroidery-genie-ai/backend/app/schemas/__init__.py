"""Pydantic request/response models."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------- auth
class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    avatar_url: str | None = None
    company: str | None = None
    onboarded: bool = False


class OrganizationOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    currency: str


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: str
    subscription: dict
    memberships: list[dict] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=160)
    company: str | None = Field(None, max_length=160)
    phone: str | None = Field(None, max_length=40)
    locale: str | None = Field(None, max_length=10)
    onboarded: bool | None = None
    preferences: dict | None = None


# ------------------------------------------------------------------- designs
class DesignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = None
    customer_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class DesignUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    description: str | None = None
    customer_id: uuid.UUID | None = None
    tags: list[str] | None = None
    status: str | None = None


class TextDesignRequest(BaseModel):
    name: str | None = Field(None, max_length=160)
    text: str = Field(..., min_length=1, max_length=200)
    font_key: str = "sans_bold"
    height_mm: float = Field(25.0, gt=2, le=400)
    letter_spacing: float = Field(0.0, ge=-0.2, le=1.0)
    line_spacing: float = Field(1.25, ge=0.8, le=3.0)
    color: str = "#111111"
    align: Literal["left", "center", "right"] = "center"
    arc_degrees: float = Field(0.0, ge=-120, le=120)
    customer_id: uuid.UUID | None = None

    @field_validator("color")
    @classmethod
    def _hex(cls, value: str) -> str:
        text = value.strip()
        if not text.startswith("#") or len(text) not in (4, 7):
            raise ValueError("Colour must be a hex string like #1A2B3C.")
        return text


class VectorizeRequest(BaseModel):
    max_colors: int = Field(6, ge=1, le=24)
    remove_background: bool = True
    background_tolerance: int = Field(28, ge=0, le=120)
    smooth: float = Field(0.35, ge=0.0, le=1.0)
    despeckle: int = Field(2, ge=0, le=8)
    target_width_mm: float | None = Field(None, gt=1, le=1000)


class DigitizeRequest(BaseModel):
    fabric: str = "cotton_shirt"
    thread_catalog: str = "genie_standard"
    machine_id: uuid.UUID | None = None
    target_width_mm: float | None = Field(None, gt=1, le=1000)
    target_height_mm: float | None = Field(None, gt=1, le=1000)
    fill_angle_deg: float = Field(45.0, ge=0, le=180)
    density_scale: float = Field(1.0, ge=0.5, le=2.5)
    auto_underlay: bool = True
    auto_satin: bool = True
    max_colors: int | None = Field(None, ge=1, le=24)


class ExportRequest(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["dst", "pes"])
    machine_id: uuid.UUID | None = None
    preview_background: str = "white"
    order_id: uuid.UUID | None = None

    @field_validator("formats")
    @classmethod
    def _not_empty(cls, value: list[str]) -> list[str]:
        cleaned = [v.lower().lstrip(".") for v in value if v.strip()]
        if not cleaned:
            raise ValueError("Choose at least one export format.")
        return cleaned


class MockupRequest(BaseModel):
    template: str = "tshirt_left_chest"
    garment_color: str = "black"
    design_width_mm: float | None = Field(None, gt=5, le=500)


# ------------------------------------------------------------------ machines
class MachineIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    brand: str = Field(..., min_length=1, max_length=60)
    model: str | None = Field(None, max_length=80)
    heads: int = Field(1, ge=1, le=40)
    needle_count: int = Field(15, ge=1, le=32)
    hoop_width_mm: float = Field(360.0, gt=10, le=2000)
    hoop_height_mm: float = Field(200.0, gt=10, le=2000)
    max_stitch_count: int = Field(200_000, ge=1000, le=2_000_000)
    max_speed_spm: int = Field(1000, ge=100, le=2000)
    supported_formats: list[str] = Field(default_factory=lambda: ["dst"])
    hourly_rate: float | None = Field(None, ge=0, le=1000)
    is_default: bool = False
    notes: str | None = None


class MachineOut(ORMModel):
    id: uuid.UUID
    name: str
    brand: str
    model: str | None
    heads: int
    needle_count: int
    hoop_width_mm: float
    hoop_height_mm: float
    max_stitch_count: int
    max_speed_spm: int
    supported_formats: list[str]
    hourly_rate: float | None
    is_default: bool
    notes: str | None


# ----------------------------------------------------------------- customers
class CustomerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    company: str | None = Field(None, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    address: dict = Field(default_factory=dict)
    tax_exempt: bool = False
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)


class CustomerOut(ORMModel):
    id: uuid.UUID
    name: str
    company: str | None
    email: str | None
    phone: str | None
    address: dict
    tax_exempt: bool
    notes: str | None
    tags: list[str]


# ------------------------------------------------------------------ products
class ProductIn(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=160)
    category: str | None = Field(None, max_length=60)
    fabric_profile: str = "cotton_shirt"
    color: str | None = Field(None, max_length=60)
    size: str | None = Field(None, max_length=20)
    blank_cost: float = Field(0, ge=0, le=100000)
    stock_quantity: int = Field(0, ge=0)
    reorder_level: int = Field(0, ge=0)
    supplier: str | None = Field(None, max_length=160)
    active: bool = True


class ProductOut(ORMModel):
    id: uuid.UUID
    sku: str
    name: str
    category: str | None
    fabric_profile: str
    color: str | None
    size: str | None
    blank_cost: float
    stock_quantity: int
    reorder_level: int
    supplier: str | None
    active: bool


class StockAdjust(BaseModel):
    delta: int
    reason: str | None = Field(None, max_length=200)


# -------------------------------------------------------------------- orders
class OrderItemIn(BaseModel):
    design_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    description: str = Field(..., min_length=1, max_length=255)
    quantity: int = Field(1, ge=1, le=100000)
    unit_price: float = Field(0, ge=0)
    unit_cost: float = Field(0, ge=0)
    placement: str | None = Field(None, max_length=80)
    pricing_breakdown: dict = Field(default_factory=dict)


class OrderIn(BaseModel):
    customer_id: uuid.UUID | None = None
    due_date: date | None = None
    rush: bool = False
    notes: str | None = None
    placement: str | None = Field(None, max_length=80)
    assigned_machine_id: uuid.UUID | None = None
    discount: float = Field(0, ge=0)
    tax: float = Field(0, ge=0)
    items: list[OrderItemIn] = Field(default_factory=list)


class OrderStatusUpdate(BaseModel):
    status: Literal[
        "quote", "approved", "digitizing", "sewing", "completed", "delivered", "cancelled"
    ]
    note: str | None = Field(None, max_length=500)


# ------------------------------------------------------------------ invoices
class InvoiceIn(BaseModel):
    order_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    issue_date: date | None = None
    due_date: date | None = None
    tax: float = Field(0, ge=0)
    notes: str | None = None
    line_items: list[dict] = Field(default_factory=list)


class InvoicePayment(BaseModel):
    amount: float = Field(..., gt=0)
    note: str | None = None


# ------------------------------------------------------------------- pricing
class PricingRequest(BaseModel):
    design_id: uuid.UUID | None = None
    stitch_count: int | None = Field(None, ge=0, le=5_000_000)
    color_count: int = Field(1, ge=1, le=32)
    trim_count: int = Field(0, ge=0)
    quantity: int = Field(1, ge=1, le=100000)
    blank_cost: float = Field(0, ge=0)
    fabric: str = "cotton_shirt"
    machine_id: uuid.UUID | None = None
    machine_speed_spm: int = Field(800, ge=100, le=2000)
    heads: int = Field(1, ge=1, le=40)
    labor_rate_per_hour: float | None = Field(None, ge=0, le=1000)
    machine_rate_per_hour: float | None = Field(None, ge=0, le=1000)
    digitizing_fee: float | None = Field(None, ge=0, le=10000)
    digitizing_already_paid: bool = False
    wholesale_margin: float = Field(0.35, ge=0, le=0.95)
    retail_margin: float = Field(0.55, ge=0, le=0.95)
    rush: bool = False


# --------------------------------------------------------------------- voice
class VoiceRequest(BaseModel):
    transcript: str = Field(..., max_length=500)


# ------------------------------------------------------------------- generic
class Message(BaseModel):
    detail: str


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int
