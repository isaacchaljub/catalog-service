"""The MCP transport in app/mcp_server.py.

Same guarantees as tests/test_endpoints.py, checked through the MCP surface
instead of REST: auth is enforced on the mounted sub-app (which FastAPI's
router-level `Depends` cannot reach), tool schemas stay flat, and every tool
returns exactly what its REST twin returns for the same input.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-at-least-24-characters-long"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {
    **AUTH,
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture(scope="module")
def client():
    os.environ["CATALOG_API_TOKEN"] = TOKEN
    os.environ["PUBLIC_BASE_URL"] = "https://catalog.example.com"
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _rpc(client, method, params=None, *, headers=MCP_HEADERS):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def _rpc_result(response):
    """Unwrap the SSE-framed JSON-RPC response streamable-http sends back."""
    for line in response.text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            assert "error" not in payload, payload["error"]
            return payload["result"]
    raise AssertionError(f"no data frame in response: {response.text!r}")


def _list_tools(client):
    return _rpc_result(_rpc(client, "tools/list"))["tools"]


def _call_tool(client, name, arguments):
    result = _rpc_result(_rpc(client, "tools/call", {"name": name, "arguments": arguments}))
    return json.loads(result["content"][0]["text"])


# --- Authentication --------------------------------------------------------------


def test_no_token_is_rejected(client):
    headers = {k: v for k, v in MCP_HEADERS.items() if k != "Authorization"}
    assert _rpc(client, "tools/list", headers=headers).status_code == 401


def test_wrong_token_is_rejected(client):
    headers = {**MCP_HEADERS, "Authorization": "Bearer wrong"}
    assert _rpc(client, "tools/list", headers=headers).status_code == 401


def test_correct_token_is_accepted(client):
    assert _rpc(client, "tools/list").status_code == 200


def test_public_pages_stay_open_despite_the_root_mount(client):
    """The bearer guard must not leak onto the pages a shopper's browser loads.

    The MCP app is mounted at "/" (see the comment on the mount in app/main.py), so
    its middleware would 401 the whole service if these routes were not registered
    ahead of it. They are, and they must stay that way.
    """
    for path in ("/p/HL-001", "/p/HL-001/image.svg", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 200, path


def test_root_mount_swallows_unknown_paths(client):
    """Consequence of mounting at "/" rather than "/mcp", pinned deliberately.

    Mounting at "/mcp" instead would answer a bare `POST /mcp` with a 307 to
    "/mcp/", which not every MCP client follows - so the root mount is the right
    trade. The cost is that an unmatched path reaches the MCP app and is judged by
    its middleware first: 401 without a token, a normal 404 with one. If this ever
    flips to a plain 404 without a token, the mount has been moved and `POST /mcp`
    should be re-checked for a redirect.
    """
    assert client.get("/totally-unknown-path").status_code == 401
    assert client.get("/totally-unknown-path", headers=AUTH).status_code == 404


# --- Tool listing ------------------------------------------------------------------


def test_exposes_exactly_the_five_tools(client):
    names = {tool["name"] for tool in _list_tools(client)}
    assert names == {
        "get_categories",
        "search_products",
        "get_products_by_category",
        "get_product_details",
        "find_similar_products",
    }


def test_tool_schemas_are_flat(client):
    """Same failure already documented in README.md: a `$ref`/`allOf`-bearing
    schema errors out before the configured runtime ever makes an HTTP call."""
    raw = json.dumps([tool["inputSchema"] for tool in _list_tools(client)])
    assert "$ref" not in raw
    assert "allOf" not in raw


# --- Parity with the REST twins -----------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "arguments", "rest_path", "rest_params"),
    [
        ("get_categories", {}, "/categories", {}),
        ("search_products", {"query": "lamp"}, "/products/search", {"query": "lamp"}),
        (
            "get_products_by_category",
            {"category": "Home & Living"},
            "/products/by-category",
            {"category": "Home & Living"},
        ),
        ("get_product_details", {"product_id": "HL-003"}, "/products/HL-003", {}),
        (
            "find_similar_products",
            {"product_id": "TG-022"},
            "/products/TG-022/similar",
            {},
        ),
    ],
)
def test_tool_matches_its_rest_twin(client, tool_name, arguments, rest_path, rest_params):
    mcp_result = _call_tool(client, tool_name, arguments)
    rest_result = client.get(rest_path, params=rest_params, headers=AUTH).json()
    assert mcp_result == rest_result


# --- base_url comes from settings, never the request --------------------------------


def test_urls_are_absolute(client):
    """Deriving base_url from the incoming request instead of settings is the bug
    documented in MCP-BRIEF.md: PUBLIC_BASE_URL silently defaults to localhost and
    every product_url/image_url comes back wrong with no error anywhere."""
    product = _call_tool(client, "get_product_details", {"product_id": "HL-003"})["product"]
    assert product["product_url"].startswith("https://catalog.example.com/")
    assert product["image_url"].startswith("https://catalog.example.com/")
