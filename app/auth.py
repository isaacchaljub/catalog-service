"""Bearer token authentication.

A bearer token is a shared secret: possession is the whole authorization. That is
appropriate here because there is exactly one consumer (the indigo.ai workspace)
and no per-caller identity to model. It is only safe over HTTPS, which Cloud Run
provides.

Out of scope, and stated as such in the README: rotation, per-client keys, request
signing, rate limiting.
"""

from __future__ import annotations

import logging
import secrets as pysecrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description="Send the API token as `Authorization: Bearer <token>`.",
)

_expected_token: str = ""

UNAUTHORIZED_DETAIL = "Missing or invalid bearer token. Send 'Authorization: Bearer <token>'."


def configure(token: str) -> None:
    """Called once at startup, after the token has been validated."""
    global _expected_token
    _expected_token = token


def _token_is_valid(token: str) -> bool:
    # Constant-time: a normal == exits at the first wrong character, which leaks the
    # token to anyone who can measure response times.
    return bool(token) and pysecrets.compare_digest(token, _expected_token)


async def verify_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """Reject anything without the exact token.

    This is the one place the service returns a non-2xx, because a missing or wrong
    token is a deployment problem the agent cannot talk its way out of. Every other
    failure mode returns 200 with a recoverable body - see app/errors.py.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=UNAUTHORIZED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    if not _token_is_valid(credentials.credentials):
        raise unauthorized


class BearerAuthASGIMiddleware:
    """Guards ASGI apps mounted with `app.mount(...)`, which `tools`'s router-level
    `Depends(verify_bearer)` never sees - FastAPI dispatches straight to a mounted
    sub-app's own ASGI callable, bypassing the router (and its dependencies)
    entirely. The MCP transport is mounted, not routed, so it needs this instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header = Headers(scope=scope).get("authorization", "").strip()
        scheme, _, token = header.partition(" ")
        # Tolerate whitespace a console paste can leave behind. The token itself is
        # opaque and never contains spaces, so stripping cannot make a wrong token right.
        token = token.strip()
        if scheme.lower() != "bearer" or not _token_is_valid(token):
            # Shape only, never the token. A 401 from a remote MCP client is otherwise
            # indistinguishable from a routing problem, and the platform reports both
            # as a bare "Not Connected".
            log.warning(
                "MCP auth rejected: scheme=%r token_len=%d expected_len=%d header_present=%s",
                scheme,
                len(token),
                len(_expected_token),
                bool(header),
            )
            response = JSONResponse(
                {"detail": UNAUTHORIZED_DETAIL},
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)
