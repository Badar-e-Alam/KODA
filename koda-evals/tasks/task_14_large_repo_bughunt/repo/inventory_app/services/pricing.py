"""Order pricing — subtotal + applied promo."""
from __future__ import annotations

from inventory_app.repository.products import get_product
from inventory_app.repository.discounts import get_discount
from inventory_app.services.discount import apply_discount


def _subtotal(items: list[dict]) -> float:
    """Sum unit_price * qty for every line item, in dollars."""
    total = 0.0
    for line in items:
        product = get_product(line["sku"])
        total += product.unit_price_dollars * line["qty"]
    return round(total, 2)


def compute_total(items: list[dict], promo: str | None = None) -> float:
    """Return the order total in dollars after applying any promo."""
    sub = _subtotal(items)
    if promo is None:
        return sub
    discount = get_discount(promo)
    if discount is None:
        return sub
    return apply_discount(sub, discount)
