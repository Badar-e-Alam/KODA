"""Module 05 — notifications resource handlers.

This module exposes the HTTP endpoints for managing notifications entities.
Persistence and integration is delegated to the Twilio backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/notifications

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "notifications"
backend = persistence_for("Twilio")


@dataclass
class NotificationsPayload:
    """Inbound payload for the notifications endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/notifications")
def create_notifications(payload: NotificationsPayload, user=current_user()) -> dict:
    """Create a new notifications record. Returns the new id and timestamp."""
    _log.info("create_notifications requested by %s", user.id)
    record = backend.insert("notifications", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Twilio", "ok": True}
