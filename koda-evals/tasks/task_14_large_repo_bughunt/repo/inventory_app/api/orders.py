"""Order submission entry point.

Wraps the pricing service into the response shape the order-confirmation
page renders.
"""
from __future__ import annotations

from inventory_app.services.pricing import compute_total
from inventory_app.repository.orders import save_order
from inventory_app.utils.logger import audit


def submit_order(items: list[dict], promo: str | None = None) -> dict:
    """Compute the order total and persist a new order record.

    Args:
        items: list of {"sku": str, "qty": int} dicts.
        promo: optional promo code (e.g., "SAVE10").

    Returns:
        {"id": int, "total": float, "status": "submitted"}
    """
    total = compute_total(items, promo)
    audit(f"submit_order total={total}")
    record = save_order(items=items, promo=promo, total=total)
    return {"id": record.id, "total": total, "status": "submitted"}
