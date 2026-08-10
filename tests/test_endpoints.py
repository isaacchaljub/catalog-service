"""HTTP surface, authentication, and the quality of the OpenAPI document itself.

The spec is the deliverable that gets pasted into indigo.ai, so it is tested like
one: every operation must carry the metadata a model needs to decide whether to
call it and with what.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

TOKEN = "test-token-at-least-24-characters-long"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def client():
    os.environ["CATALOG_API_TOKEN"] = TOKEN
    os.environ["PUBLIC_BASE_URL"] = "https://catalog.example.com"
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def spec(client):
    return client.get("/openapi.json").json()


# --- Authentication ------------------------------------------------------------


def test_no_token_is_rejected(client):
    assert client.get("/categories").status_code == 401


def test_wrong_token_is_rejected(client):
    assert client.get("/categories", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_wrong_scheme_is_rejected(client):
    assert client.get("/categories", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


def test_correct_token_is_accepted(client):
    assert client.get("/categories", headers=AUTH).status_code == 200


def test_401_tells_the_caller_how_to_authenticate(client):
    body = client.get("/categories").json()
    assert "Bearer" in body["detail"]


@pytest.mark.parametrize("path", ["/categories", "/products/search", "/products/HL-003"])
def test_every_tool_endpoint_is_protected(client, path):
    assert client.get(path).status_code == 401


def test_spec_and_health_are_public(client):
    """The importer fetches the spec before it has anywhere to put a credential."""
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200


# --- The OpenAPI document ------------------------------------------------------


def test_spec_is_valid_openapi(spec):
    """Validate the document, do not eyeball it.

    The post-processing in app/openapi_compat.py rewrites the generated schema, and a
    global `title` strip once removed `info.title` - which OpenAPI requires. The
    result was an invalid document, and indigo.ai's importer reported it with the
    same opaque "There is an error with inserted Schema" it gives for everything
    else. Hours went into guessing at that message; this test replaces the guessing.
    """
    from openapi_spec_validator import validate

    validate(spec)


def test_required_info_fields_survive_post_processing(spec):
    assert spec["info"]["title"]
    assert spec["info"]["version"]


def test_spec_declares_openapi_30(spec):
    """indigo.ai's own example declares 3.0.0; FastAPI would emit 3.1 by default."""
    assert spec["openapi"] == "3.0.0"


def test_spec_carries_an_absolute_server_url(spec):
    """The spec is pasted, not fetched, so a relative URL leaves nowhere to call."""
    assert spec["servers"][0]["url"] == "https://catalog.example.com"


def test_spec_exposes_exactly_the_five_tools(spec):
    operations = {
        op["operationId"]
        for path in spec["paths"].values()
        for op in path.values()
        if "operationId" in op
    }
    assert operations == {
        "get_categories",
        "search_products",
        "get_products_by_category",
        "get_product_details",
        "find_similar_products",
    }


def _operations(spec):
    return [op for path in spec["paths"].values() for op in path.values() if "operationId" in op]


def test_every_operation_has_a_summary_and_description(spec):
    for operation in _operations(spec):
        assert operation.get("summary"), operation["operationId"]
        assert len(operation.get("description", "")) > 120, operation["operationId"]


def test_every_parameter_is_described(spec):
    """Parameter descriptions are the whole basis for how a model fills them in."""
    for operation in _operations(spec):
        for parameter in operation.get("parameters", []):
            described = parameter.get("description") or parameter.get("schema", {}).get("description")
            assert described, f"{operation['operationId']}.{parameter['name']}"


def test_bearer_security_is_declared_and_applied(spec):
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    for operation in _operations(spec):
        assert operation.get("security") == [{"bearerAuth": []}], operation["operationId"]


def test_spec_contains_no_openapi_31_constructs(spec):
    """3.0.0 is a promise about the body, not just the version string.

    Setting `openapi_version` alone leaves Pydantic v2 emitting JSON Schema 2020-12
    shapes. indigo.ai's importer validates against 3.0 and rejects the mismatch with
    "There is an error with inserted Schema", so this is enforced rather than assumed.
    """
    raw = json.dumps(spec)
    assert '"type": "null"' not in raw
    assert '"const"' not in raw
    assert '"$schema"' not in raw
    assert '"prefixItems"' not in raw

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for bound in ("exclusiveMinimum", "exclusiveMaximum"):
                if bound in node:
                    assert isinstance(node[bound], bool), f"{bound} must be boolean in 3.0"
            for combiner in ("anyOf", "oneOf"):
                for member in node.get(combiner, []) or []:
                    assert member != {"type": "null"}, "null member survived the downgrade"
            for value in node.values():
                walk(value)

    walk(spec)


def test_spec_carries_nothing_a_tool_runtime_rejects(spec):
    """The importer accepts OpenAPI; the model runtime accepts far less.

    indigo.ai hands the imported document to the configured LLM as function
    declarations, and those runtimes take only a narrow subset of JSON Schema. These
    three keywords are safe to remove because nothing depends on them.

    `allOf` is *not* in this list, deliberately: removing it would mean putting
    `nullable`/`description` next to a `$ref`, which OpenAPI 3.0 forbids and the
    importer rejects outright. See `test_no_ref_has_sibling_keys`.
    """
    raw = json.dumps(spec)
    for construct in ('"additionalProperties"', '"exclusiveMinimum"'):
        assert construct not in raw, f"{construct} breaks LLM function-calling runtimes"

    # `title` goes only from schema objects. `info.title` is required by OpenAPI, and
    # removing it is what made the document invalid in the first place.
    assert spec["info"]["title"]
    schemas = json.dumps(spec.get("components", {}).get("schemas", {}))
    assert '"title"' not in schemas
    assert '"title"' not in json.dumps(spec["paths"])


def test_no_ref_has_sibling_keys(spec):
    """In OpenAPI 3.0 a `$ref` must stand alone; siblings are ignored and validators reject it.

    Pydantic emits `$ref` beside a `description` for model-typed fields, and an
    earlier attempt to strip `allOf` produced the same shape. Both made indigo.ai's
    importer fail with "There is an error with inserted Schema" - a document that
    will not import is worth nothing, however clean it looks.
    """

    def walk(node, path="$"):
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        elif isinstance(node, dict):
            if "$ref" in node:
                assert len(node) == 1, f"$ref with siblings {sorted(node)} at {path}"
            for key, value in node.items():
                walk(value, f"{path}.{key}")

    walk(spec)


def test_deep_response_schemas_are_collapsed(spec):
    """`suggestions` and `alternatives` are read at runtime, never constructed.

    The importer inlines every `$ref`, so a full schema for them cost several
    expanded copies of ProductSummary and bought nothing. The description survives,
    which is the part a model actually reads.
    """
    schemas = spec["components"]["schemas"]
    suggestions = schemas["ProductListResponse"]["properties"]["suggestions"]
    assert suggestions["type"] == "object"
    assert "properties" not in suggestions
    assert suggestions["description"]

    # The payload the agent recommends from stays fully described.
    assert "pitch" in schemas["ProductSummary"]["properties"]
    assert "stock_level" in schemas["ProductSummary"]["properties"]


def test_spec_does_not_advertise_a_422_it_never_returns(spec):
    """Validation failures come back as 200 `invalid_parameter`, so 422 is a lie."""
    for operation in _operations(spec):
        assert "422" not in operation.get("responses", {}), operation["operationId"]
    assert "HTTPValidationError" not in spec.get("components", {}).get("schemas", {})


def test_committed_spec_is_current(spec):
    """openapi.json is a generated snapshot, so it must never drift from the code.

    The served document is the deliverable; the file exists so reviewers can read the
    spec without curl and so it can be pasted into the tool importer. Regenerate with
    `uv run python scripts/export_openapi.py`.
    """
    committed = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))

    assert committed["servers"][0]["url"].startswith("https://"), (
        "Committed spec carries a non-production server URL. The importer pastes this "
        "document, so a localhost URL would leave the platform nowhere to call."
    )
    # `servers` legitimately differs: the test app is configured with a stub URL.
    assert {k: v for k, v in committed.items() if k != "servers"} == {
        k: v for k, v in spec.items() if k != "servers"
    }, "openapi.json is stale - run scripts/export_openapi.py"


def test_enums_are_declared_so_the_model_can_read_them(spec):
    search = spec["paths"]["/products/search"]["get"]
    sort = next(p for p in search["parameters"] if p["name"] == "sort")
    assert set(sort["schema"]["enum"]) == {"relevance", "price_asc", "price_desc", "rating"}


# --- Response behaviour over HTTP ----------------------------------------------


def test_responses_never_contain_nulls(client):
    """`null` invites invention; an absent key does not."""
    for path, params in [
        ("/categories", {}),
        ("/products/search", {"limit": 12}),
        ("/products/HL-003", {}),
        ("/products/TG-022", {}),
        ("/products/TG-022/similar", {}),
    ]:
        body = client.get(path, params=params, headers=AUTH).text
        assert ": null" not in body, path


def test_unknown_category_is_a_recoverable_200(client):
    response = client.get(
        "/products/by-category", params={"category": "Sorcery Supplies"}, headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown_category"
    assert len(body["available_categories"]) == 11


def test_unknown_product_is_a_recoverable_200(client):
    response = client.get("/products/ZZ-999", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "not_found"


def test_invalid_enum_becomes_a_recoverable_200(client):
    """The spec declares the enum; the runtime still helps when the model ignores it."""
    response = client.get("/products/search", params={"sort": "cheapest"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid_parameter"
    assert body["did_you_mean"] == ["relevance", "price_asc", "price_desc", "rating"]


def test_search_route_is_not_shadowed_by_the_product_id_route(client):
    """`/products/search` must not be read as a product whose id is 'search'."""
    body = client.get("/products/search", headers=AUTH).json()
    assert body["status"] == "ok"
    assert "products" in body


def test_health_reports_data_quality(client):
    body = client.get("/health").json()
    assert body["catalog"]["products_loaded"] == 152
    assert body["catalog"]["quarantined_total"] == 0
    assert body["catalog"]["duplicate_groups"] == 2


# --- The /p/ product page -------------------------------------------------------


def test_product_page_needs_no_auth(client):
    """Clicked by a shopper's browser, not called by the model - no bearer token."""
    response = client.get("/p/HL-001")
    assert response.status_code == 200
    assert "Aurora Table Lamp" in response.text


def test_product_page_404s_for_unknown_product(client):
    response = client.get("/p/ZZ-999")
    assert response.status_code == 404
    assert "ZZ-999" in response.text


def test_product_page_is_not_a_tool(spec):
    """Human-facing HTML, not something the model should ever call."""
    assert "/p/{product_id}" not in spec["paths"]


def test_product_page_shows_alternatives_for_out_of_stock_items(client):
    response = client.get("/p/HL-004")  # Linen Fig Candle, out of stock in the current export
    assert response.status_code == 200
    assert "Agotado" in response.text


def test_product_url_is_returned_from_the_tools(client):
    body = client.get("/products/search", params={"query": "lamp"}, headers=AUTH).json()
    assert body["products"][0]["product_url"] == "https://catalog.example.com/p/HL-001"


# --- The /p/.../image.svg placeholder -------------------------------------------


def test_placeholder_image_needs_no_auth(client):
    response = client.get("/p/HL-001/image.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_placeholder_image_404s_for_unknown_product(client):
    assert client.get("/p/ZZ-999/image.svg").status_code == 404


def test_placeholder_image_is_not_a_tool(spec):
    assert "/p/{product_id}/image.svg" not in spec["paths"]


def test_two_different_categories_get_different_placeholder_colours(client):
    """The whole point: two products in one reply must not look identical."""
    lamp = client.get("/p/HL-001/image.svg").text  # Home & Living
    knife = client.get("/p/KD-001/image.svg").text  # Kitchen & Dining
    assert lamp != knife


def test_image_url_is_returned_from_the_tools(client):
    body = client.get("/products/search", params={"query": "lamp"}, headers=AUTH).json()
    assert body["products"][0]["image_url"] == "https://catalog.example.com/p/HL-001/image.svg"


def test_placeholder_image_escapes_untrusted_catalogue_text():
    """Same 'assumed hostile' export - an unescaped '&' would also break the SVG's XML."""
    from app.placeholder_image import render

    svg = render("<script>alert(1)</script> & Co", "Home & Living")
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert " & " not in svg  # bare & is invalid inside SVG text content


def test_product_page_escapes_untrusted_catalogue_text():
    """The CSV is one export, assumed hostile - HTML in a field must not execute."""
    from app.product_page import render_product

    hostile = {
        "product_id": "HL-001",
        "name": "<script>alert(1)</script>",
        "price_eur": 10.0,
        "description": "<img src=x onerror=alert(1)>",
    }
    html = render_product(hostile, alternatives=None)
    assert "<script>" not in html
    assert "<img src=x" not in html  # the hostile one; the page's own hero <img> is fine
    assert "&lt;script&gt;" in html
