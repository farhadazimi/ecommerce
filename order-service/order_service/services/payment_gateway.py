"""Payment gateway abstraction.

``SimulatedPaymentGateway`` is a deterministic stand-in for a real PSP. Replacing it with
a real provider means implementing :class:`PaymentGateway` and selecting it in
``build_gateway`` - the order workflow does not change.

Test cards (any future expiry, any CVV):

=====================  =========================
4242 4242 4242 4242    success (Visa)
5555 5555 5555 4444    success (Mastercard)
4000 0000 0000 0002    declined: CARD_DECLINED
4000 0000 0000 9995    declined: INSUFFICIENT_FUNDS
4000 0000 0000 0069    declined: EXPIRED_CARD
=====================  =========================
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class CardDetails:
    number: str
    holder: str
    expiry_month: int
    expiry_year: int
    cvv: str

    @property
    def digits(self) -> str:
        return "".join(ch for ch in self.number if ch.isdigit())


@dataclass(frozen=True)
class ChargeResult:
    success: bool
    transaction_id: str | None = None
    card_last4: str | None = None
    card_brand: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    name: str

    def charge(self, amount: Decimal, currency: str, card: CardDetails, reference: str) -> ChargeResult: ...


def luhn_valid(number: str) -> bool:
    if not number.isdigit() or not 12 <= len(number) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def card_brand(number: str) -> str:
    if number.startswith("4"):
        return "VISA"
    if number[:2] in {"51", "52", "53", "54", "55"} or number[:4].isdigit() and 2221 <= int(number[:4]) <= 2720:
        return "MASTERCARD"
    if number[:2] in {"34", "37"}:
        return "AMEX"
    return "CARD"


class SimulatedPaymentGateway:
    name = "SIMULATOR"

    DECLINES = {
        "4000000000000002": ("CARD_DECLINED", "The card was declined"),
        "4000000000009995": ("INSUFFICIENT_FUNDS", "The card has insufficient funds"),
        "4000000000000069": ("EXPIRED_CARD", "The card has expired"),
    }

    def charge(self, amount: Decimal, currency: str, card: CardDetails, reference: str) -> ChargeResult:
        number = card.digits
        last4 = number[-4:] if len(number) >= 4 else None
        brand = card_brand(number)
        if not luhn_valid(number):
            return ChargeResult(False, card_last4=last4, card_brand=brand, failure_code="INVALID_CARD_NUMBER",
                                failure_reason="The card number is invalid")
        now = datetime.now(UTC)
        if (card.expiry_year, card.expiry_month) < (now.year, now.month):
            return ChargeResult(False, card_last4=last4, card_brand=brand, failure_code="EXPIRED_CARD",
                                failure_reason="The card has expired")
        if number in self.DECLINES:
            code, reason = self.DECLINES[number]
            return ChargeResult(False, card_last4=last4, card_brand=brand, failure_code=code, failure_reason=reason)
        if amount <= 0:
            return ChargeResult(False, card_last4=last4, card_brand=brand, failure_code="INVALID_AMOUNT",
                                failure_reason="Invalid amount")
        return ChargeResult(True, transaction_id=f"SIM-{secrets.token_hex(8).upper()}", card_last4=last4, card_brand=brand)


def build_gateway() -> PaymentGateway:
    return SimulatedPaymentGateway()
