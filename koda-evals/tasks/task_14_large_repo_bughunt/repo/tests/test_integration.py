"""Integration test for the order-submission flow.

Run with: ``pytest tests/test_integration.py -q`` from the repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the in-repo package is importable when the agent runs pytest
# from the repo root (the eval harness's chdir).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inventory_app.api.orders import submit_order


def test_submit_order_applies_promo_correctly():
    """Submitting two WIDGET-1 ($20 each) with SAVE10 should total $36.00.

    Math: 2 * 20.00 = 40.00. 10% off = 4.00 reduction. 40.00 - 4.00 = 36.00.

    A failing assertion here means the promo discount is not being
    applied with the expected magnitude. Trace through the call chain
    to find where the percent value diverges from the convention
    documented on the Discount model — DO NOT modify this test.
    """
    order = submit_order(
        items=[{"sku": "WIDGET-1", "qty": 2}],
        promo="SAVE10",
    )
    assert order["status"] == "submitted"
    assert order["total"] == 36.00, (
        f"expected total 36.00 (2 * 20.00 * 0.9), got {order['total']}"
    )


def test_submit_order_without_promo():
    """Sanity check: no promo means no discount."""
    order = submit_order(
        items=[{"sku": "WIDGET-2", "qty": 1}],
        promo=None,
    )
    assert order["total"] == 35.50
