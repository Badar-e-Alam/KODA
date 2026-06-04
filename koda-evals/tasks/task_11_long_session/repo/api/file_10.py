"""Module 10 — teams resource handlers.

This module exposes the HTTP endpoints for managing teams entities.
Persistence and integration is delegated to the Postgres backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/teams
  - GET /api/teams
  - PUT /api/teams
  - DELETE /api/teams

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "teams"
backend = persistence_for("Postgres")


@dataclass
class TeamsPayload:
    """Inbound payload for the teams endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/teams")
def create_teams(payload: TeamsPayload, user=current_user()) -> dict:
    """Create a new teams record. Returns the new id and timestamp."""
    _log.info("create_teams requested by %s", user.id)
    record = backend.insert("teams", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/teams/<int:id>")
def read_teams(id: int, user=current_user()) -> dict:
    """Fetch a single teams record by id."""
    _log.info("read_teams for %s", id)
    record = backend.get("teams", id)
    return record.to_dict() if record else {}


@router.put("/api/teams/<int:id>")
def update_teams(id: int, payload: TeamsPayload, user=current_user()) -> dict:
    """Replace a teams record with the provided payload."""
    _log.info("update_teams on %s by %s", id, user.id)
    backend.update("teams", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/teams/<int:id>")
def delete_teams(id: int, user=current_user()) -> dict:
    """Soft-delete a teams record."""
    _log.info("delete_teams on %s by %s", id, user.id)
    backend.soft_delete("teams", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Postgres", "ok": True}
