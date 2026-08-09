"""Bearer token authentication.

A bearer token is a shared secret: possession is the whole authorization. That is
appropriate here because there is exactly one consumer (the indigo.ai workspace)
and no per-caller identity to model. It is only safe over HTTPS, which Cloud Run
provides.

Out of scope, and stated as such in the README: rotation, per-client keys, request
signing, rate limiting.
"""

from __future__ import annotations

import secrets as pysecrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description="Send the API token as `Authorization: Bearer <token>`.",
)

_expected_token: str = ""


def configure(token: str) -> None:
    """Called once at startup, after the token has been validated."""
    global _expected_token
    _expected_token = token


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
        detail="Missing or invalid bearer token. Send 'Authorization: Bearer <token>'.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    # Constant-time: a normal == exits at the first wrong character, which leaks the
    # token to anyone who can measure response times.
    if not pysecrets.compare_digest(credentials.credentials, _expected_token):
        raise unauthorized
