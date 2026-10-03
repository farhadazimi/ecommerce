from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    shipping_name: str = Field(min_length=2, max_length=120)
    shipping_phone: str | None = Field(default=None, max_length=32)
    shipping_address_line1: str = Field(min_length=3, max_length=255)
    shipping_address_line2: str | None = Field(default=None, max_length=255)
    shipping_city: str = Field(min_length=2, max_length=100)
    shipping_postal_code: str = Field(min_length=2, max_length=20)
    shipping_country: str = Field(min_length=2, max_length=100)
    notes: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=64)


class CancelBody(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class StatusUpdate(BaseModel):
    status: Literal["PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"]
    note: str | None = Field(default=None, max_length=255)


class PaymentCreateBody(BaseModel):
    order_id: int = Field(gt=0)


class PaymentConfirmBody(BaseModel):
    payment_id: int = Field(gt=0)
    card_number: str = Field(min_length=12, max_length=23, pattern=r"^[0-9 ]+$")
    card_holder: str = Field(min_length=2, max_length=120)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)
    cvv: str = Field(min_length=3, max_length=4, pattern=r"^[0-9]+$")
