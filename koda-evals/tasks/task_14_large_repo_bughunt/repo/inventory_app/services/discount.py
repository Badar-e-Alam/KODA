"""Apply a discount to an already-rounded subtotal.

Discount conventions
--------------------
A ``Discount`` object carries a single ``percent`` field. ``percent`` is
always an INTEGER on the 0-100 scale — i.e., ``percent=10`` means "ten
percent off", not "ten-thousandths off".

Repositories that store discounts as fractional ratios (e.g., 0.10
meaning ten percent) are out-of-spec; treat any such value as a bug at
the repository, not a magic-number to compensate for here.
"""
from __future__ import annotations

from inventory_app.models.discount import Discount


def apply_discount(subtotal: float, discount: Discount) -> float:
    """Return ``subtotal`` with ``discount.percent`` taken off, rounded."""
    reduction = subtotal * (discount.percent / 100.0)
    return round(subtotal - reduction, 2)
