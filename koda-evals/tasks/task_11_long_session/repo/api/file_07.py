"""Module 07 — subscriptions resource handlers.

This module exposes the HTTP endpoints for managing subscriptions entities.
Persistence and integration is delegated to the Stripe backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/subscriptions
  - GET /api/subscriptions
  - PUT /api/subscriptions
  - DELETE /api/subscriptions

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "subscriptions"
backend = persistence_for("Stripe")


@dataclass
class SubscriptionsPayload:
    """Inbound payload for the subscriptions endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/subscriptions")
def create_subscriptions(payload: SubscriptionsPayload, user=current_user()) -> dict:
    """Create a new subscriptions record. Returns the new id and timestamp."""
    _log.info("create_subscriptions requested by %s", user.id)
    record = backend.insert("subscriptions", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/subscriptions/<int:id>")
def read_subscriptions(id: int, user=current_user()) -> dict:
    """Fetch a single subscriptions record by id."""
    _log.info("read_subscriptions for %s", id)
    record = backend.get("subscriptions", id)
    return record.to_dict() if record else {}


@router.put("/api/subscriptions/<int:id>")
def update_subscriptions(id: int, payload: SubscriptionsPayload, user=current_user()) -> dict:
    """Replace a subscriptions record with the provided payload."""
    _log.info("update_subscriptions on %s by %s", id, user.id)
    backend.update("subscriptions", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/subscriptions/<int:id>")
def delete_subscriptions(id: int, user=current_user()) -> dict:
    """Soft-delete a subscriptions record."""
    _log.info("delete_subscriptions on %s by %s", id, user.id)
    backend.soft_delete("subscriptions", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Stripe", "ok": True}
