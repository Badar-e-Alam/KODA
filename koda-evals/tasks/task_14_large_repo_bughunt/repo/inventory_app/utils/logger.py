"""Tiny audit-log shim.

In production this would write to the structured logging pipeline; here
we just append to an in-memory list so tests can assert on it.
"""
from __future__ import annotations

_AUDIT_LOG: list[str] = []


def audit(message: str) -> None:
    _AUDIT_LOG.append(message)


def get_audit_log() -> list[str]:
    return list(_AUDIT_LOG)
