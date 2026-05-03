"""Module 15 — webhooks resource handlers.

This module exposes the HTTP endpoints for managing webhooks entities.
Persistence and integration is delegated to the AWS-SNS backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/webhooks
  - GET /api/webhooks
  - DELETE /api/webhooks

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "webhooks"
backend = persistence_for("AWS-SNS")


@dataclass
class WebhooksPayload:
    """Inbound payload for the webhooks endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/webhooks")
def create_webhooks(payload: WebhooksPayload, user=current_user()) -> dict:
    """Create a new webhooks record. Returns the new id and timestamp."""
    _log.info("create_webhooks requested by %s", user.id)
    record = backend.insert("webhooks", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/webhooks/<int:id>")
def read_webhooks(id: int, user=current_user()) -> dict:
    """Fetch a single webhooks record by id."""
    _log.info("read_webhooks for %s", id)
    record = backend.get("webhooks", id)
    return record.to_dict() if record else {}


@router.delete("/api/webhooks/<int:id>")
def delete_webhooks(id: int, user=current_user()) -> dict:
    """Soft-delete a webhooks record."""
    _log.info("delete_webhooks on %s by %s", id, user.id)
    backend.soft_delete("webhooks", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "AWS-SNS", "ok": True}
