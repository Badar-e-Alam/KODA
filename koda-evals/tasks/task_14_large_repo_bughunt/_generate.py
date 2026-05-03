"""One-shot generator for the bug-hunt repo. Run from this directory."""
from pathlib import Path
import textwrap

ROOT = Path(__file__).parent / "repo"

# Layout:
#   inventory_app/          (production code, ~25 files)
#   tests/test_integration.py  (one test, fails)

# ── core (bug lives in repository/discounts.py) ─────────────────────────

CORE_FILES: dict[str, str] = {}

CORE_FILES["inventory_app/__init__.py"] = (
    '"""Inventory backend — orders, pricing, discounts, persistence."""\n'
    '__version__ = "0.1.0"\n'
)

CORE_FILES["inventory_app/api/__init__.py"] = '"""HTTP-shaped entry points."""\n'

CORE_FILES["inventory_app/api/orders.py"] = '''"""Order submission entry point.

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
'''

CORE_FILES["inventory_app/services/__init__.py"] = '"""Domain services."""\n'

CORE_FILES["inventory_app/services/pricing.py"] = '''"""Order pricing — subtotal + applied promo."""
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
'''

CORE_FILES["inventory_app/services/discount.py"] = '''"""Apply a discount to an already-rounded subtotal.

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
'''

CORE_FILES["inventory_app/repository/__init__.py"] = '"""Backing-store accessors."""\n'

CORE_FILES["inventory_app/repository/products.py"] = '''"""Product lookup. In-memory mock for tests."""
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
'''

# THE BUG: SAVE10 stores percent as 0.10 (a ratio) instead of 10 (the
# integer-percent convention documented on Discount and in
# services/discount.py). The other codes are correct, so the agent has to
# notice the inconsistency. Fix is changing 0.10 → 10 below.
CORE_FILES["inventory_app/repository/discounts.py"] = '''"""Promo-code lookup. In-memory mock for tests."""
from __future__ import annotations

from inventory_app.models.discount import Discount


_DISCOUNTS: dict[str, Discount] = {
    "SAVE10":  Discount(code="SAVE10",  percent=0.10, description="10% off everything"),
    "SAVE25":  Discount(code="SAVE25",  percent=25,   description="Loyalty member 25% off"),
    "FRESH50": Discount(code="FRESH50", percent=50,   description="First-time buyer half off"),
}


def get_discount(code: str) -> Discount | None:
    return _DISCOUNTS.get(code.upper())
'''

CORE_FILES["inventory_app/repository/orders.py"] = '''"""Persisted orders. In-memory mock."""
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
'''

CORE_FILES["inventory_app/models/__init__.py"] = '"""Domain types shared across packages."""\n'

CORE_FILES["inventory_app/models/discount.py"] = '''"""Discount domain type."""
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
'''

CORE_FILES["inventory_app/utils/__init__.py"] = '"""Generic utilities used across packages."""\n'

CORE_FILES["inventory_app/utils/logger.py"] = '''"""Tiny audit-log shim.

In production this would write to the structured logging pipeline; here
we just append to an in-memory list so tests can assert on it.
"""
from __future__ import annotations

_AUDIT_LOG: list[str] = []


def audit(message: str) -> None:
    _AUDIT_LOG.append(message)


def get_audit_log() -> list[str]:
    return list(_AUDIT_LOG)
'''

# ── distractor files (~17 of them, plausible but unrelated to the bug) ──

DISTRACTORS = {
    # api/
    "inventory_app/api/customers.py": "customers HTTP handlers",
    "inventory_app/api/products.py": "product catalogue HTTP handlers",
    "inventory_app/api/health.py": "liveness probe",

    # services/
    "inventory_app/services/notifications.py": "outbound notifications",
    "inventory_app/services/audit_service.py": "audit submission service",
    "inventory_app/services/inventory.py": "stock-on-hand calculations",

    # repository/
    "inventory_app/repository/customers.py": "customer lookup",

    # utils/
    "inventory_app/utils/cache.py": "in-process cache helper",
    "inventory_app/utils/validation.py": "input validation helpers",
    "inventory_app/utils/formatting.py": "currency/date formatting",
    "inventory_app/utils/retries.py": "retry decorator with backoff",

    # config/
    "inventory_app/config/__init__.py": "config package",
    "inventory_app/config/settings.py": "feature flags + env-derived config",

    # extra models
    "inventory_app/models/customer.py": "Customer dataclass",
    "inventory_app/models/order.py": "Order dataclass + status enum",
    "inventory_app/models/product.py": "Product dataclass with computed fields",

    # background workers
    "inventory_app/workers/__init__.py": "workers package",
    "inventory_app/workers/email_worker.py": "queue worker for emails",
    "inventory_app/workers/cleanup_worker.py": "periodic GC of stale orders",
}


def render_distractor(rel_path: str, summary: str) -> str:
    """Generate ~70 lines of plausible code for a distractor file."""
    name = rel_path.split("/")[-1].replace(".py", "")
    cls = "".join(part.title() for part in name.split("_"))
    body = f'''"""{summary.title()} module.

Stub-quality implementation used by the integration test repo. The
production version of this module talks to live infra (S3, SES, the
Postgres replica, etc.); here we keep the surface area realistic enough
that someone walking the codebase couldn't tell at a glance whether
anything in this file matters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class {cls}Config:
    """Knobs the {cls.lower()} module reads at startup."""
    enabled: bool = True
    timeout_s: float = 5.0
    retries: int = 3


class {cls}:
    """{summary.capitalize()}.

    The class exposes a small, synchronous API (the async variant lives
    in workers/). Most call sites go through the convenience function
    ``{name}(...)`` defined at module scope.
    """

    def __init__(self, config: {cls}Config | None = None) -> None:
        self.config = config or {cls}Config()

    def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch ``payload`` and return the raw provider response."""
        if not self.config.enabled:
            _log.info("%s.call: short-circuit, disabled", type(self).__name__)
            return {{"status": "skipped"}}
        # The real implementation would talk to the upstream service here.
        # For the tests we just acknowledge the call.
        return {{"status": "ok", "echo": payload}}

    def healthcheck(self) -> bool:
        return self.config.enabled


_default = {cls}()


def {name}(payload: dict[str, Any]) -> dict[str, Any]:
    """Module-level convenience wrapper around the default {cls} instance."""
    return _default.call(payload)
'''
    return body


# Tests file — single integration test that fails because of the bug.
TEST_FILE = '''"""Integration test for the order-submission flow.

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
'''


def write(rel: str, content: str) -> None:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def main() -> None:
    if ROOT.exists():
        # Wipe and regenerate so the script is idempotent.
        import shutil
        shutil.rmtree(ROOT)
    for rel, content in CORE_FILES.items():
        write(rel, content)
    for rel, summary in DISTRACTORS.items():
        if rel.endswith("__init__.py"):
            write(rel, f'"""{summary}."""\n')
        else:
            write(rel, render_distractor(rel, summary))
    write("tests/__init__.py", "")
    write("tests/test_integration.py", TEST_FILE)

    # Tally
    files = sorted(ROOT.rglob("*.py"))
    total = sum(p.stat().st_size for p in files)
    print(f"  generated {len(files)} files, {total} bytes ({total/1024:.1f} KB)")
    for p in files:
        size = p.stat().st_size
        print(f"    {p.relative_to(ROOT)}: {size} B")


if __name__ == "__main__":
    main()
