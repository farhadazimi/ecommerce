"""Pricing rules shared by the cart (backend) and order placement (order service)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .config import Settings

CENT = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def shipping_fee(subtotal: Decimal, settings: Settings) -> Decimal:
    if subtotal <= 0 or subtotal >= settings.free_shipping_threshold:
        return money(0)
    return money(settings.shipping_flat_fee)
