"""Promo-code lookup. In-memory mock for tests."""
from __future__ import annotations

from inventory_app.models.discount import Discount


_DISCOUNTS: dict[str, Discount] = {
    "SAVE10":  Discount(code="SAVE10",  percent=0.10, description="10% off everything"),
    "SAVE25":  Discount(code="SAVE25",  percent=25,   description="Loyalty member 25% off"),
    "FRESH50": Discount(code="FRESH50", percent=50,   description="First-time buyer half off"),
}


def get_discount(code: str) -> Discount | None:
    return _DISCOUNTS.get(code.upper())
