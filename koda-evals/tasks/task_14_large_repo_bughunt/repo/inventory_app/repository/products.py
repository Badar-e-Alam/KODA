"""Product lookup. In-memory mock for tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    unit_price_dollars: float


_CATALOG: dict[str, Product] = {
    "WIDGET-1": Product(sku="WIDGET-1", name="Widget v1", unit_price_dollars=20.00),
    "WIDGET-2": Product(sku="WIDGET-2", name="Widget v2", unit_price_dollars=35.50),
    "GADGET-A": Product(sku="GADGET-A", name="Gadget A",  unit_price_dollars=99.99),
}


def get_product(sku: str) -> Product:
    if sku not in _CATALOG:
        raise KeyError(f"unknown sku {sku!r}")
    return _CATALOG[sku]
