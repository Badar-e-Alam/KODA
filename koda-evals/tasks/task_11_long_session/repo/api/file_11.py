"""Module 11 — projects resource handlers.

This module exposes the HTTP endpoints for managing projects entities.
Persistence and integration is delegated to the Postgres backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/projects
  - GET /api/projects
  - PUT /api/projects
  - DELETE /api/projects

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "projects"
backend = persistence_for("Postgres")


@dataclass
class ProjectsPayload:
    """Inbound payload for the projects endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/projects")
def create_projects(payload: ProjectsPayload, user=current_user()) -> dict:
    """Create a new projects record. Returns the new id and timestamp."""
    _log.info("create_projects requested by %s", user.id)
    record = backend.insert("projects", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/projects/<int:id>")
def read_projects(id: int, user=current_user()) -> dict:
    """Fetch a single projects record by id."""
    _log.info("read_projects for %s", id)
    record = backend.get("projects", id)
    return record.to_dict() if record else {}


@router.put("/api/projects/<int:id>")
def update_projects(id: int, payload: ProjectsPayload, user=current_user()) -> dict:
    """Replace a projects record with the provided payload."""
    _log.info("update_projects on %s by %s", id, user.id)
    backend.update("projects", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/projects/<int:id>")
def delete_projects(id: int, user=current_user()) -> dict:
    """Soft-delete a projects record."""
    _log.info("delete_projects on %s by %s", id, user.id)
    backend.soft_delete("projects", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Postgres", "ok": True}
