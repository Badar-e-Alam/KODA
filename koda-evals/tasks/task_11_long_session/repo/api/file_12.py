"""Module 12 — tags resource handlers.

This module exposes the HTTP endpoints for managing tags entities.
Persistence and integration is delegated to the Redis backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/tags
  - GET /api/tags
  - PUT /api/tags
  - DELETE /api/tags

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "tags"
backend = persistence_for("Redis")


@dataclass
class TagsPayload:
    """Inbound payload for the tags endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/tags")
def create_tags(payload: TagsPayload, user=current_user()) -> dict:
    """Create a new tags record. Returns the new id and timestamp."""
    _log.info("create_tags requested by %s", user.id)
    record = backend.insert("tags", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/tags/<int:id>")
def read_tags(id: int, user=current_user()) -> dict:
    """Fetch a single tags record by id."""
    _log.info("read_tags for %s", id)
    record = backend.get("tags", id)
    return record.to_dict() if record else {}


@router.put("/api/tags/<int:id>")
def update_tags(id: int, payload: TagsPayload, user=current_user()) -> dict:
    """Replace a tags record with the provided payload."""
    _log.info("update_tags on %s by %s", id, user.id)
    backend.update("tags", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/tags/<int:id>")
def delete_tags(id: int, user=current_user()) -> dict:
    """Soft-delete a tags record."""
    _log.info("delete_tags on %s by %s", id, user.id)
    backend.soft_delete("tags", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Redis", "ok": True}
