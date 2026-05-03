"""Discount domain type."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Discount:
    """A promo discount.

    Fields:
        code: case-insensitive identifier; stored upper-cased in the
            repository.
        percent: INTEGER on the 0-100 scale. ``Discount(percent=10)``
            means ten percent off. The pricing services rely on this
            convention — passing a fractional ratio (e.g. 0.10) will
            silently produce almost-no-discount.
        description: human-readable label for receipts/audit logs.
    """
    code: str
    percent: int
    description: str = ""
