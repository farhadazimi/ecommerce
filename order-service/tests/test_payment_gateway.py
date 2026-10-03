from decimal import Decimal

import pytest

from order_service.services.payment_gateway import CardDetails, SimulatedPaymentGateway, card_brand, luhn_valid


def card(number: str, year: int = 2035, month: int = 12) -> CardDetails:
    return CardDetails(number=number, holder="T", expiry_month=month, expiry_year=year, cvv="123")


@pytest.mark.parametrize("number,valid", [("4242424242424242", True), ("5555555555554444", True),
                                          ("4242424242424241", False), ("abcd", False), ("4242", False)])
def test_luhn(number, valid):
    assert luhn_valid(number) is valid


def test_brand_detection():
    assert card_brand("4242424242424242") == "VISA"
    assert card_brand("5555555555554444") == "MASTERCARD"
    assert card_brand("378282246310005") == "AMEX"


@pytest.mark.parametrize("number,code", [
    ("4000 0000 0000 0002", "CARD_DECLINED"),
    ("4000 0000 0000 9995", "INSUFFICIENT_FUNDS"),
    ("4000 0000 0000 0069", "EXPIRED_CARD"),
    ("4242 4242 4242 4241", "INVALID_CARD_NUMBER"),
])
def test_declines(number, code):
    result = SimulatedPaymentGateway().charge(Decimal("10"), "USD", card(number), "ref")
    assert not result.success and result.failure_code == code


def test_expired_card_by_date():
    result = SimulatedPaymentGateway().charge(Decimal("10"), "USD", card("4242424242424242", year=2020), "ref")
    assert result.failure_code == "EXPIRED_CARD"


def test_success():
    result = SimulatedPaymentGateway().charge(Decimal("10"), "USD", card("4242 4242 4242 4242"), "ref")
    assert result.success and result.card_last4 == "4242" and result.transaction_id.startswith("SIM-")
