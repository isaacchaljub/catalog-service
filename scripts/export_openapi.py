#!/usr/bin/env python
"""Write the served OpenAPI document to ./openapi.json.

The specification the service serves at /openapi.json is the real deliverable - it
is generated from the route definitions, so it cannot drift from the code. This
script commits a snapshot of it alongside the source for two reasons: reviewers can
read the spec without running curl, and it can be pasted into the indigo.ai tool
importer directly.

The snapshot is generated, never hand-edited. `test_committed_spec_is_current`
fails if the two diverge.

Usage:
    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOYED_BASE_URL = "https://catalog-service-566410667338.europe-west1.run.app"


def main() -> int:
    # The spec is pasted rather than fetched by the importer, so it must carry the
    # deployed base URL - not localhost, and not a relative path.
    os.environ.setdefault("PUBLIC_BASE_URL", DEPLOYED_BASE_URL)
    # Never used: generating the document only introspects routes, it does not serve
    # traffic. Present so the import does not trip the startup token check.
    os.environ.setdefault("CATALOG_API_TOKEN", "spec-generation-only-not-a-real-token")

    sys.path.insert(0, str(ROOT))
    from app.main import app

    spec = app.openapi()
    destination = ROOT / "openapi.json"
    destination.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    operations = sorted(
        operation["operationId"]
        for path in spec["paths"].values()
        for operation in path.values()
        if "operationId" in operation
    )
    print(f"wrote {destination.relative_to(ROOT)} ({destination.stat().st_size:,} bytes)")
    print(f"  openapi:  {spec['openapi']}")
    print(f"  server:   {spec['servers'][0]['url']}")
    print(f"  tools:    {', '.join(operations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
