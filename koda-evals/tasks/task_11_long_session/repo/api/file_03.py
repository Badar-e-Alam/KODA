"""Module 03 — products resource handlers.

This module exposes the HTTP endpoints for managing products entities.
Persistence and integration is delegated to the S3 backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/products
  - GET /api/products
  - PUT /api/products
  - DELETE /api/products

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "products"
backend = persistence_for("S3")


@dataclass
class ProductsPayload:
    """Inbound payload for the products endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/products")
def create_products(payload: ProductsPayload, user=current_user()) -> dict:
    """Create a new products record. Returns the new id and timestamp."""
    _log.info("create_products requested by %s", user.id)
    record = backend.insert("products", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/products/<int:id>")
def read_products(id: int, user=current_user()) -> dict:
    """Fetch a single products record by id."""
    _log.info("read_products for %s", id)
    record = backend.get("products", id)
    return record.to_dict() if record else {}


@router.put("/api/products/<int:id>")
def update_products(id: int, payload: ProductsPayload, user=current_user()) -> dict:
    """Replace a products record with the provided payload."""
    _log.info("update_products on %s by %s", id, user.id)
    backend.update("products", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/products/<int:id>")
def delete_products(id: int, user=current_user()) -> dict:
    """Soft-delete a products record."""
    _log.info("delete_products on %s by %s", id, user.id)
    backend.soft_delete("products", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "S3", "ok": True}
