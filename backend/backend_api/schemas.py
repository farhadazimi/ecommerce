"""Request / response models (Pydantic) for the public REST API."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator

MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float)]
T = TypeVar("T")

_NAME_RE = re.compile(r"^[^<>{}]*$")  # no markup in plain-text fields


def _plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not _NAME_RE.match(value):
        raise ValueError("must not contain < > { } characters")
    return value


def _check_password(v: str) -> str:
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("password must contain at least one letter and one digit")
    return v


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class Message(BaseModel):
    message: str


class ErrorBody(BaseModel):
    code: str = Field(examples=["INSUFFICIENT_INVENTORY"])
    message: str = Field(examples=["Only 2 unit(s) of 'Mouse' are available"])
    request_id: str | None = Field(default=None, examples=["6f1c9a0e2b0d4c1f9d1e3a5b7c9d0e1f"])
    details: object | None = Field(default=None, examples=[[{"product_id": 7, "requested": 3, "available": 2}]])


class ErrorResponse(BaseModel):
    """Envelope used by every non-2xx response."""

    error: ErrorBody


# ------------------------------------------------------------------ auth / users
class RegisterRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"email": "jane@example.com", "password": "Secret123", "full_name": "Jane Doe"}]
    })

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=2, max_length=120)

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        return _check_password(v)

    _clean_name = field_validator("full_name")(classmethod(lambda cls, v: _plain_text(v)))


class LoginRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"email": "customer@example.com", "password": "Customer123!"}]})

    email: str = Field(min_length=3, max_length=255)  # not re-validated: it only has to match an account
    password: str = Field(min_length=1, max_length=72)


class ProfileOut(BaseModel):
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country: str | None = None
    avatar_url: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None
    profile: ProfileOut | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32, pattern=r"^[0-9+()\-\s]*$")
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=100)

    _clean = field_validator(
        "full_name", "address_line1", "address_line2", "city", "postal_code", "country"
    )(classmethod(lambda cls, v: _plain_text(v)))


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        return _check_password(v)


# ------------------------------------------------------------------ catalog
class CategoryRef(BaseModel):
    id: int
    name: str
    slug: str


class CategoryOut(CategoryRef):
    description: str | None = None
    product_count: int = 0


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    _clean = field_validator("name", "description")(classmethod(lambda cls, v: _plain_text(v)))


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    _clean = field_validator("name", "description")(classmethod(lambda cls, v: _plain_text(v)))


class ImageOut(BaseModel):
    id: int
    url: str
    is_primary: bool


class StockOut(BaseModel):
    available: int
    in_stock: bool
    low_stock: bool


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    slug: str
    description: str | None
    price: MoneyOut
    currency: str
    is_active: bool
    category: CategoryRef | None
    stock: StockOut
    image_url: str | None
    images: list[ImageOut]
    created_at: datetime
    updated_at: datetime


class ProductIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "sku": "EL-2001", "name": "Bluetooth Speaker", "description": "Portable, waterproof", "price": "59.90",
        "category_id": 1, "is_active": True, "stock_quantity": 40, "low_stock_threshold": 5,
    }]})

    sku: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9\-_]+$")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    price: Decimal = Field(ge=0, le=1_000_000, decimal_places=2)
    category_id: int | None = None
    is_active: bool = True
    stock_quantity: int = Field(default=0, ge=0, le=1_000_000)
    low_stock_threshold: int = Field(default=5, ge=0, le=100_000)

    _clean = field_validator("name", "description")(classmethod(lambda cls, v: _plain_text(v)))


class ProductUpdate(BaseModel):
    """All fields optional: only provided fields are changed."""

    sku: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[A-Za-z0-9\-_]+$")
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    price: Decimal | None = Field(default=None, ge=0, le=1_000_000, decimal_places=2)
    category_id: int | None = None
    is_active: bool | None = None
    stock_quantity: int | None = Field(default=None, ge=0, le=1_000_000)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=100_000)

    _clean = field_validator("name", "description")(classmethod(lambda cls, v: _plain_text(v)))


# ------------------------------------------------------------------ cart
class CartItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=99)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)


class CartProduct(BaseModel):
    id: int
    name: str
    slug: str
    sku: str
    price: MoneyOut
    image_url: str | None
    is_active: bool


class CartItemOut(BaseModel):
    id: int
    product: CartProduct
    quantity: int
    unit_price: MoneyOut
    line_total: MoneyOut
    available: int
    in_stock: bool


class CartOut(BaseModel):
    id: int | None
    items: list[CartItemOut]
    item_count: int
    subtotal: MoneyOut
    shipping_fee: MoneyOut
    total: MoneyOut
    currency: str
    warnings: list[str] = []


# ------------------------------------------------------------------ orders / payment (proxied)
class ShippingInfo(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "shipping_name": "Jane Doe", "shipping_phone": "+98 21 1234 5678", "shipping_address_line1": "1 Cloud Street",
        "shipping_city": "Tehran", "shipping_postal_code": "10001", "shipping_country": "Iran", "notes": "Ring twice",
    }]})

    shipping_name: str = Field(min_length=2, max_length=120)
    shipping_phone: str | None = Field(default=None, max_length=32, pattern=r"^[0-9+()\-\s]*$")
    shipping_address_line1: str = Field(min_length=3, max_length=255)
    shipping_address_line2: str | None = Field(default=None, max_length=255)
    shipping_city: str = Field(min_length=2, max_length=100)
    shipping_postal_code: str = Field(min_length=2, max_length=20)
    shipping_country: str = Field(min_length=2, max_length=100)
    notes: str | None = Field(default=None, max_length=500)

    _clean = field_validator(
        "shipping_name",
        "shipping_address_line1",
        "shipping_address_line2",
        "shipping_city",
        "shipping_postal_code",
        "shipping_country",
        "notes",
    )(classmethod(lambda cls, v: _plain_text(v)))


class PaymentCreate(BaseModel):
    order_id: int = Field(gt=0)


class PaymentConfirm(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "payment_id": 12, "card_number": "4242 4242 4242 4242", "card_holder": "Jane Doe",
        "expiry_month": 12, "expiry_year": 2030, "cvv": "123",
    }]})

    payment_id: int = Field(gt=0)
    card_number: str = Field(min_length=12, max_length=23, pattern=r"^[0-9 ]+$")
    card_holder: str = Field(min_length=2, max_length=120)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)
    cvv: str = Field(min_length=3, max_length=4, pattern=r"^[0-9]+$")

    _clean = field_validator("card_holder")(classmethod(lambda cls, v: _plain_text(v)))


class OrderStatusUpdate(BaseModel):
    status: Literal["PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"]
    note: str | None = Field(default=None, max_length=255)

    _clean = field_validator("note")(classmethod(lambda cls, v: _plain_text(v)))


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)

    _clean = field_validator("reason")(classmethod(lambda cls, v: _plain_text(v)))


# ------------------------------------------------------------------ admin
class InventoryOut(BaseModel):
    product_id: int
    sku: str
    name: str
    is_active: bool
    quantity: int
    reserved: int
    available: int
    low_stock_threshold: int
    low_stock: bool
    updated_at: datetime


class InventoryUpdate(BaseModel):
    quantity: int | None = Field(default=None, ge=0, le=1_000_000, description="Set absolute on-hand quantity")
    adjust: int | None = Field(default=None, ge=-1_000_000, le=1_000_000, description="Relative change")
    low_stock_threshold: int | None = Field(default=None, ge=0, le=100_000)


class UserStatusUpdate(BaseModel):
    is_active: bool


class Statistics(BaseModel):
    model_config = ConfigDict(extra="allow")

    products: int
    active_products: int
    categories: int
    users: int
    customers: int
    orders: int
    orders_today: int
    orders_by_status: dict[str, int]
    revenue: MoneyOut
    currency: str
    low_stock_products: int
    out_of_stock_products: int
    active_users: int
    recent_orders: list[dict]
