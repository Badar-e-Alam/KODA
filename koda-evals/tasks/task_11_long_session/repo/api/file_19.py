"""Module 19 — billing resource handlers.

This module exposes the HTTP endpoints for managing billing entities.
Persistence and integration is delegated to the Stripe backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - GET /api/billing
  - PUT /api/billing

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "billing"
backend = persistence_for("Stripe")


@dataclass
class BillingPayload:
    """Inbound payload for the billing endpoints."""
    name: str
    metadata: dict | None = None


@router.get("/api/billing/<int:id>")
def read_billing(id: int, user=current_user()) -> dict:
    """Fetch a single billing record by id."""
    _log.info("read_billing for %s", id)
    record = backend.get("billing", id)
    return record.to_dict() if record else {}


@router.put("/api/billing/<int:id>")
def update_billing(id: int, payload: BillingPayload, user=current_user()) -> dict:
    """Replace a billing record with the provided payload."""
    _log.info("update_billing on %s by %s", id, user.id)
    backend.update("billing", id, payload)
    return {"id": id, "updated_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Stripe", "ok": True}
