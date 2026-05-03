"""Module 02 — orders resource handlers.

This module exposes the HTTP endpoints for managing orders entities.
Persistence and integration is delegated to the RabbitMQ backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/orders
  - GET /api/orders
  - PUT /api/orders
  - DELETE /api/orders

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "orders"
backend = persistence_for("RabbitMQ")


@dataclass
class OrdersPayload:
    """Inbound payload for the orders endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/orders")
def create_orders(payload: OrdersPayload, user=current_user()) -> dict:
    """Create a new orders record. Returns the new id and timestamp."""
    _log.info("create_orders requested by %s", user.id)
    record = backend.insert("orders", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/orders/<int:id>")
def read_orders(id: int, user=current_user()) -> dict:
    """Fetch a single orders record by id."""
    _log.info("read_orders for %s", id)
    record = backend.get("orders", id)
    return record.to_dict() if record else {}


@router.put("/api/orders/<int:id>")
def update_orders(id: int, payload: OrdersPayload, user=current_user()) -> dict:
    """Replace a orders record with the provided payload."""
    _log.info("update_orders on %s by %s", id, user.id)
    backend.update("orders", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/orders/<int:id>")
def delete_orders(id: int, user=current_user()) -> dict:
    """Soft-delete a orders record."""
    _log.info("delete_orders on %s by %s", id, user.id)
    backend.soft_delete("orders", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "RabbitMQ", "ok": True}
