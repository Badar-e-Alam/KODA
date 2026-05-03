"""Module 06 — tickets resource handlers.

This module exposes the HTTP endpoints for managing tickets entities.
Persistence and integration is delegated to the Jira backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/tickets
  - GET /api/tickets
  - PUT /api/tickets
  - DELETE /api/tickets

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "tickets"
backend = persistence_for("Jira")


@dataclass
class TicketsPayload:
    """Inbound payload for the tickets endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/tickets")
def create_tickets(payload: TicketsPayload, user=current_user()) -> dict:
    """Create a new tickets record. Returns the new id and timestamp."""
    _log.info("create_tickets requested by %s", user.id)
    record = backend.insert("tickets", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/tickets/<int:id>")
def read_tickets(id: int, user=current_user()) -> dict:
    """Fetch a single tickets record by id."""
    _log.info("read_tickets for %s", id)
    record = backend.get("tickets", id)
    return record.to_dict() if record else {}


@router.put("/api/tickets/<int:id>")
def update_tickets(id: int, payload: TicketsPayload, user=current_user()) -> dict:
    """Replace a tickets record with the provided payload."""
    _log.info("update_tickets on %s by %s", id, user.id)
    backend.update("tickets", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/tickets/<int:id>")
def delete_tickets(id: int, user=current_user()) -> dict:
    """Soft-delete a tickets record."""
    _log.info("delete_tickets on %s by %s", id, user.id)
    backend.soft_delete("tickets", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Jira", "ok": True}
