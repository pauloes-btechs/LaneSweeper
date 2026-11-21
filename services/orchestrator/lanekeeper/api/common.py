"""Shared API plumbing: principals from bearer tokens, error mapping, JSON helpers."""
from __future__ import annotations

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import AuthError, Principal, principal_from_token
from ..devices import DeviceAuthError
from ..policy.engine import PolicyDenied


def principal(authorization: str | None = Header(default=None)) -> Principal:
    token = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
    try:
        return principal_from_token(token)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e


def staff(p: Principal) -> Principal:
    if p.anonymous:
        raise HTTPException(401, "sign in required")
    return p


def jurisdiction_for(p: Principal, requested: str | None) -> str:
    """Pick the caller's jurisdiction. A requested id must be one the caller belongs to (never trusted blindly)."""
    js = p.jurisdictions()
    if requested:
        if requested not in js:
            raise HTTPException(403, "not a member of that jurisdiction")
        return requested
    if len(js) == 1:
        return next(iter(js))
    if not js:
        raise HTTPException(403, "no municipal membership")
    raise HTTPException(400, "specify jurisdiction")


def install_error_handlers(app) -> None:
    @app.exception_handler(PolicyDenied)
    async def _denied(_: Request, e: PolicyDenied):
        return JSONResponse({"error": "forbidden", "detail": str(e)}, status_code=403)

    @app.exception_handler(DeviceAuthError)
    async def _dev(_: Request, e: DeviceAuthError):
        return JSONResponse({"error": "device_auth", "detail": str(e)}, status_code=401)

    @app.exception_handler(AuthError)
    async def _auth(_: Request, e: AuthError):
        return JSONResponse({"error": "unauthorized", "detail": str(e)}, status_code=401)
