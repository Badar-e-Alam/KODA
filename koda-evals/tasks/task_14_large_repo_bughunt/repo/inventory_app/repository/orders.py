"""Persisted orders. In-memory mock."""
from __future__ import annotations

from dataclasses import dataclass, field
import itertools


_ORDER_IDS = itertools.count(start=1)
_ORDERS: dict[int, "OrderRecord"] = {}


@dataclass
class OrderRecord:
    id: int
    items: list[dict]
    promo: str | None
    total: float


def save_order(items: list[dict], promo: str | None, total: float) -> OrderRecord:
    rec = OrderRecord(
        id=next(_ORDER_IDS),
        items=list(items),
        promo=promo,
        total=total,
    )
    _ORDERS[rec.id] = rec
    return rec


def fetch_order(id: int) -> OrderRecord | None:
    return _ORDERS.get(id)
