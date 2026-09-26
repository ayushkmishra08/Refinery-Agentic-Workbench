"""Shared request helpers for the API modules (no import cycle: this file imports nothing from api.py at load time)."""
from __future__ import annotations

from fastapi import Header, HTTPException


def orch():
    from workbench.app import api

    return api.orch()


def bearer(authorization: str | None = Header(default=None)) -> str | None:
    """The token from an ``Authorization: Bearer`` header, when one was sent."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def require_role(token: str | None, minimum: str):
    """Refuse a caller below ``minimum``. Used by the endpoints that expose other people's work."""
    from workbench.security.roles import level_of, title

    o = orch()
    principal = o.principal(token)
    if not o.cfg.security.enabled:
        return principal
    if not principal.authenticated:
        raise HTTPException(401, "Sign in first: POST /auth/login.")
    if level_of(principal.role) < level_of(minimum):
        raise HTTPException(403, f"This needs {title(minimum)} or above; you are {title(principal.role)}.")
    return principal
