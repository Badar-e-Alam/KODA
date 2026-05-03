"""Module 18 — api_keys resource handlers.

This module exposes the HTTP endpoints for managing api_keys entities.
Persistence and integration is delegated to the Vault backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/api_keys
  - GET /api/api_keys
  - DELETE /api/api_keys

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "api_keys"
backend = persistence_for("Vault")


@dataclass
class ApikeysPayload:
    """Inbound payload for the api_keys endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/api_keys")
def create_api_keys(payload: ApikeysPayload, user=current_user()) -> dict:
    """Create a new api_keys record. Returns the new id and timestamp."""
    _log.info("create_api_keys requested by %s", user.id)
    record = backend.insert("api_keys", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/api_keys/<int:id>")
def read_api_keys(id: int, user=current_user()) -> dict:
    """Fetch a single api_keys record by id."""
    _log.info("read_api_keys for %s", id)
    record = backend.get("api_keys", id)
    return record.to_dict() if record else {}


@router.delete("/api/api_keys/<int:id>")
def delete_api_keys(id: int, user=current_user()) -> dict:
    """Soft-delete a api_keys record."""
    _log.info("delete_api_keys on %s by %s", id, user.id)
    backend.soft_delete("api_keys", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Vault", "ok": True}
