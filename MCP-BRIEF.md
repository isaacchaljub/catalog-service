# Brief: add an MCP server to catalog-service

Expose the existing catalogue as MCP tools, mounted on the FastAPI app that already
serves the REST surface. This is a **second transport in front of existing code**, not a
new feature. Nothing about ranking, filtering or response shape changes.

`app/search.py`'s module docstring already says *"The REST endpoints and the MCP server
both call these"* — that is aspirational today. Your job is to make it true.

Environment is verified: **fastmcp 3.4.6** is already in `pyproject.toml` and currently
imported by nothing.

---

## The five tools

Names and descriptions **must match the existing OpenAPI operations exactly**. The agent
prompt and its conversation examples already reference these names, and the descriptions
are tuned prompt surface — do not reword them.

| Tool | Calls in `app/search.py` |
|---|---|
| `get_categories` | `get_categories(catalog)` |
| `search_products` | `search_products(catalog, *, query, category, occasion, recipient, max_price_eur, min_price_eur, include_out_of_stock, sort, limit, base_url)` |
| `get_products_by_category` | `products_by_category(...)` — note the function name differs from the tool name |
| `get_product_details` | `get_product_details(catalog, product_id, *, base_url)` |
| `find_similar_products` | `find_similar_products(catalog, *, product_id, max_price_eur, include_out_of_stock, limit, base_url)` |

Take the descriptions verbatim from `openapi.json` (`summary` on each operation), e.g.

> `search_products` — Find gift products matching a shopper's constraints: budget,
> occasion, recipient, category or free text.

**Do not reimplement any filtering or ranking.** Every tool is a thin adapter: take
arguments, call the `app/search.py` function, return its dict.

---

## Five traps, all verified against this codebase

### 1. Auth — a mounted sub-app bypasses the router guard

`app/main.py:223` protects REST with a router-level dependency:

```python
tools = APIRouter(dependencies=[Depends(auth.verify_bearer)], tags=["catalog"])
```

**Router dependencies do not apply to mounted ASGI apps.** A plain
`app.mount("/mcp", mcp_app)` leaves the entire MCP surface unauthenticated while REST
stays locked — the whole catalogue publicly readable, and nothing looks broken.

`FastMCP.http_app()` takes a `middleware=` argument for exactly this. Put the bearer
check there, reusing the expected token from `app/auth.py` rather than re-reading env.

**Ship a test that a request with no token is rejected.** That is the regression guard.

### 2. Lifespan must be handed to the parent app

`http_app()` returns a type named `StarletteWithLifespan`. Mounting it without wiring its
lifespan into the parent FastAPI app leaves the MCP session manager uninitialised: the
mount exists, and every call fails.

### 3. `stateless_http=True`

Deployment is Cloud Run with scale-to-zero and `--max-instances 3`, so consecutive
requests can land on different instances. Stateful MCP sessions assume affinity that does
not exist here.

### 4. `base_url` comes from settings, never from the request

Every REST handler passes `base_url=_settings.public_base_url` (`app/main.py:372`, `442`,
`533`). The MCP tools must do the same.

Deriving it from the incoming request reintroduces a bug that cost an hour on 10 Aug 2026:
`PUBLIC_BASE_URL` defaults to `http://localhost:8080`, nothing fails loudly, every call
returns 200, and only `image_url` / `product_url` are wrong. In the widget that shows up
as a broken image rendering its `alt` text next to the link, so the product name appears
twice, plus a Chrome "wants to access other apps and services on this device" prompt.

### 5. Flat JSON Schemas — no `$ref`, no `allOf`

Same failure already documented in `README.md`: the tool importer accepted a spec
containing `allOf`, and the configured Gemini runtime then refused to build function
declarations from it, erroring before any HTTP call. Different surface, identical
incompatibility. Keep every tool's input schema flat and inline.

Also honour `MAX_RESPONSE_BYTES = 12_288` (`app/search.py:26`) — the existing helpers
already enforce it; do not bypass them.

---

## Mounting

Mount at **`/mcp`** on the existing app. The platform's Connection field takes the **bare
service URL** and appends the transport path itself (`INDIGO.md:124`) — giving it the full
path is the documented trap.

Platform side, for reference (already documented, no code needed):

```
Agent Settings -> Integrations -> Add MCP Server
  Name        gift-catalog
  Connection  https://catalog-service-566410667338.europe-west1.run.app
  Headers     Authorization: Bearer {{secrets.CATALOG_API_TOKEN}}
```

---

## Tests

Mirror `tests/test_endpoints.py`. At minimum:

- each of the five tools returns the same payload as its REST twin for one known input
- a call with no bearer token is rejected (guards trap 1)
- `image_url` and `product_url` are absolute and start with `https://` (guards trap 4)
- the tool list exposes exactly the five expected names

Run the full suite before finishing — it is currently green at 134 tests.

---

## Deploying

**`--update-env-vars`, never `--set-env-vars`** — the latter replaces the whole
environment and silently drops `PUBLIC_BASE_URL`. The correct command is in
`README.md` under Deployment.
