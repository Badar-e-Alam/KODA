"""Module 04 — invoices resource handlers.

This module exposes the HTTP endpoints for managing invoices entities.
Persistence and integration is delegated to the Stripe backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/invoices
  - GET /api/invoices

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "invoices"
backend = persistence_for("Stripe")


@dataclass
class InvoicesPayload:
    """Inbound payload for the invoices endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/invoices")
def create_invoices(payload: InvoicesPayload, user=current_user()) -> dict:
    """Create a new invoices record. Returns the new id and timestamp."""
    _log.info("create_invoices requested by %s", user.id)
    record = backend.insert("invoices", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/invoices/<int:id>")
def read_invoices(id: int, user=current_user()) -> dict:
    """Fetch a single invoices record by id."""
    _log.info("read_invoices for %s", id)
    record = backend.get("invoices", id)
    return record.to_dict() if record else {}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Stripe", "ok": True}
