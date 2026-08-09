"""Runtime configuration, read from the environment.

Validation is deliberately split in two: `Settings.load()` never raises, so tests and
the ingest pipeline can run without a token, while `require_api_token()` is called
once at application startup and refuses to boot a public service with a weak or
missing bearer token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Gift Shop Catalog Service"
APP_VERSION = "1.0.0"

MIN_TOKEN_LENGTH = 24


@dataclass(frozen=True)
class Settings:
    api_token: str
    csv_path: Path
    pitches_path: Path
    port: int
    public_base_url: str

    @classmethod
    def load(cls) -> Settings:
        return cls(
            api_token=os.environ.get("CATALOG_API_TOKEN", ""),
            csv_path=Path(os.environ.get("CATALOG_CSV_PATH", "data/gift-shop-catalog.csv")),
            pitches_path=Path(os.environ.get("CATALOG_PITCHES_PATH", "data/pitches.json")),
            port=int(os.environ.get("PORT", "8080")),
            # The spec is pasted into indigo.ai rather than fetched, so it must carry
            # an absolute base URL - a relative one leaves the platform nowhere to call.
            public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
        )


def require_api_token(settings: Settings) -> str:
    """Fail fast at startup rather than serving an unprotected public endpoint."""
    token = settings.api_token
    if not token:
        raise RuntimeError(
            "CATALOG_API_TOKEN is not set. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"CATALOG_API_TOKEN is {len(token)} characters; "
            f"at least {MIN_TOKEN_LENGTH} are required for a publicly reachable service."
        )
    return token
