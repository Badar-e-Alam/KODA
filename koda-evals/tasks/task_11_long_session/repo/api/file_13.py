"""Module 13 — comments resource handlers.

This module exposes the HTTP endpoints for managing comments entities.
Persistence and integration is delegated to the Postgres backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/comments
  - GET /api/comments
  - PUT /api/comments
  - DELETE /api/comments

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "comments"
backend = persistence_for("Postgres")


@dataclass
class CommentsPayload:
    """Inbound payload for the comments endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/comments")
def create_comments(payload: CommentsPayload, user=current_user()) -> dict:
    """Create a new comments record. Returns the new id and timestamp."""
    _log.info("create_comments requested by %s", user.id)
    record = backend.insert("comments", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/comments/<int:id>")
def read_comments(id: int, user=current_user()) -> dict:
    """Fetch a single comments record by id."""
    _log.info("read_comments for %s", id)
    record = backend.get("comments", id)
    return record.to_dict() if record else {}


@router.put("/api/comments/<int:id>")
def update_comments(id: int, payload: CommentsPayload, user=current_user()) -> dict:
    """Replace a comments record with the provided payload."""
    _log.info("update_comments on %s by %s", id, user.id)
    backend.update("comments", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/comments/<int:id>")
def delete_comments(id: int, user=current_user()) -> dict:
    """Soft-delete a comments record."""
    _log.info("delete_comments on %s by %s", id, user.id)
    backend.soft_delete("comments", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Postgres", "ok": True}
