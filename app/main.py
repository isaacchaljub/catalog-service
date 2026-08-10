"""HTTP surface.

Endpoints are deliberately thin: parse parameters, call `app/search.py`, apply the
response budget. All logic lives in pure functions so the MCP server and the tests
exercise exactly the code the REST API does.

Everything a model reads - operation ids, summaries, descriptions, parameter
descriptions - is written here, because the OpenAPI document generated from this
file *is* the tool definition that gets pasted into indigo.ai.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import auth, placeholder_image, product_page, search
from app.config import APP_NAME, APP_VERSION, Settings, require_api_token
from app.ingest import Catalog, load_catalog
from app.openapi_compat import (
    downgrade_to_openapi_30,
    remove_validation_error_responses,
    simplify_for_tool_runtimes,
)
from app.models import (
    CategoriesResponse,
    HealthResponse,
    ProductDetailResponse,
    ProductListResponse,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("catalog")

_catalog: Catalog | None = None
_settings: Settings = Settings.load()


def get_catalog() -> Catalog:
    if _catalog is None:  # pragma: no cover - only reachable outside the lifespan
        raise RuntimeError("Catalogue not loaded.")
    return _catalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _catalog, _settings
    _settings = Settings.load()
    auth.configure(require_api_token(_settings))
    _catalog = load_catalog(_settings.csv_path, _settings.pitches_path)
    app.servers = [{"url": _settings.public_base_url, "description": "Catalog Service"}]
    app.openapi_schema = None          # drop any document cached before servers was set
    yield


SERVICE_DESCRIPTION = """
Read-only access to an online gift shop's product catalogue, built for a shop
assistant that helps someone choose a present.

Start with `get_categories` to learn the exact category, occasion and recipient
values this catalogue uses, then use `search_products` for everything else.

Every operation returns HTTP 200 with a `status` field. `status: "ok"` means the
result is usable; anything else means nothing was returned and the response
explains what to do instead - read `message`, `suggestions` and `notes` and act on
them rather than telling the shopper there is nothing. The only error status is
401, returned when the bearer token is missing or wrong.
""".strip()

app = FastAPI(
    title="Gift Shop Catalog Service",
    version=APP_VERSION,
    description=SERVICE_DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)
# indigo.ai's own example spec declares 3.0.0. FastAPI emits 3.1 by default, which
# many importers reject; matching the consumer's documented version costs nothing.
app.openapi_version = "3.0.0"
app.servers = [{"url": _settings.public_base_url, "description": "Catalog Service"}]


def custom_openapi() -> dict[str, Any]:
    """Serve a document that genuinely conforms to the version it declares.

    `openapi_version` alone only changes the version string - Pydantic v2 keeps
    emitting JSON Schema 2020-12 constructs that OpenAPI 3.0 validators reject.
    See app/openapi_compat.py.
    """
    if app.openapi_schema:
        return app.openapi_schema
    schema = remove_validation_error_responses(FastAPI.openapi(app))
    schema = downgrade_to_openapi_30(schema)
    app.openapi_schema = simplify_for_tool_runtimes(schema)
    return app.openapi_schema


app.openapi = custom_openapi


def _allowed_values(error: dict[str, Any]) -> list[str]:
    """Pull the legal values out of a Pydantic literal error.

    Pydantic renders them as one string - "'a', 'b' or 'c'" - so it has to be taken
    apart to give the model a clean list it can retry from.
    """
    expected = (error.get("ctx") or {}).get("expected", "")
    if not isinstance(expected, str) or not expected:
        return []
    return [
        value.strip().strip("'\"")
        for value in expected.replace(" or ", ", ").split(",")
        if value.strip()
    ]


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Turn FastAPI's 422 into a recoverable 200.

    Enums stay declared in the schema, so the spec a model reads is precise. But if
    it still sends a bad value, telling it which parameter and which values are
    allowed is worth far more than a status code the platform may not surface.
    """
    first = exc.errors()[0] if exc.errors() else {}
    location = [str(part) for part in first.get("loc", []) if part not in ("query", "path", "body")]
    parameter = location[-1] if location else "parameter"
    allowed = _allowed_values(first)

    return JSONResponse(
        status_code=200,
        content={
            "status": "invalid_parameter",
            "message": (
                f"'{first.get('input')}' is not a valid value for {parameter}. "
                f"{first.get('msg', '')}".strip()
            ),
            "total_matches": 0,
            "returned": 0,
            "products": [],
            "did_you_mean": allowed or None,
            "notes": [
                f"Correct the {parameter} parameter and call the tool again. "
                "Call get_categories if you need the list of valid values."
            ],
        },
    )


@app.get(
    "/health",
    operation_id="health",
    summary="Liveness check and catalogue data-quality summary.",
    response_model=HealthResponse,
    include_in_schema=False,
    tags=["operations"],
)
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "catalog": get_catalog().report.summary(),
    }


@app.get("/p/{product_id}", include_in_schema=False, response_class=HTMLResponse)
async def product_detail_page(product_id: str) -> HTMLResponse:
    """Human-facing page a shopper lands on from a `product_url`. Not a tool - no
    auth, not in the spec. Reuses search.get_product_details, the same function the
    search_products/get_product_details tools call, so this page can never disagree
    with what the agent already said.
    """
    payload = search.get_product_details(
        get_catalog(), product_id, base_url=_settings.public_base_url
    )
    if payload["status"] != "ok":
        return HTMLResponse(product_page.render_not_found(product_id), status_code=404)
    return HTMLResponse(
        product_page.render_product(payload["product"], payload.get("alternatives"))
    )


@app.get("/p/{product_id}/image.svg", include_in_schema=False)
async def product_placeholder_image(product_id: str) -> Response:
    """Generated placeholder art behind `image_url`. Not a tool, no auth - an <img>
    or a markdown image reference fetches this directly.
    """
    product = get_catalog().get(product_id)
    if product is None:
        return Response(status_code=404)
    svg = placeholder_image.render(product.name, product.category)
    return Response(content=svg, media_type="image/svg+xml")


tools = APIRouter(dependencies=[Depends(auth.verify_bearer)], tags=["catalog"])


GET_CATEGORIES_DESCRIPTION = """
Returns every category in the gift catalogue - how many products it holds, how many
are in stock, its price range in euros, and its subcategories. Also returns
`filter_vocabulary`: the exact strings accepted by the `occasion` and `recipient`
parameters of the other tools.

Call this once near the start of a gift-finding conversation, before your first
`search_products` call. It is cheap, and it is the only reliable way to learn the
exact category, occasion and recipient values this catalogue uses. Guessing them -
"homeware", "sister", "new home" - returns nothing.

Does not return individual products. Use `search_products` for those.
""".strip()


@tools.get(
    "/categories",
    operation_id="get_categories",
    summary="List every category with counts, price ranges, and the exact filter values this catalogue uses.",
    description=GET_CATEGORIES_DESCRIPTION,
    response_model=CategoriesResponse,
    response_model_exclude_none=True,
)
async def get_categories() -> dict[str, Any]:
    return search.enforce_budget(search.get_categories(get_catalog()))


SEARCH_DESCRIPTION = """
The primary product-discovery tool. Use it as soon as the shopper has given you
*any* constraint: a budget, an occasion, who it is for, a category, or just a
description of what they want ("something cosy for a new flat").

All parameters are optional and combine with AND. Omit anything the shopper has not
told you - do not invent values to fill parameters, because every filter you add
narrows the result. Out-of-stock products are excluded unless you explicitly
include them.

Returns at most `limit` products plus `total_matches`, so you know how much you did
not see, and `notes` telling you how to narrow a broad result. When nothing
matches, `status` is `"no_match"` and the response carries concrete suggestions for
relaxing the search - act on those rather than telling the shopper there is nothing.

Prefer this over `get_products_by_category` whenever the shopper has said anything
beyond naming a category.
""".strip()


@tools.get(
    "/products/search",
    operation_id="search_products",
    summary="Find gift products matching a shopper's constraints - budget, occasion, recipient, category or free text.",
    description=SEARCH_DESCRIPTION,
    response_model=ProductListResponse,
    response_model_exclude_none=True,
)
async def search_products(
    query: Annotated[
        str | None,
        Query(
            description="Free text describing what the shopper wants, in their own words "
            "where possible - 'cosy blanket for a new flat', 'chef's knife'. Matched "
            "against product names, tags, subcategories and descriptions. Leave empty if "
            "they have given only structured constraints such as a budget.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        Query(
            description="Category display name ('Home & Living') or slug ('home-living'). "
            "Minor misspellings are tolerated. Use a value from get_categories.",
        ),
    ] = None,
    occasion: Annotated[
        str | None,
        Query(
            description="One of the values in filter_vocabulary.occasions from "
            "get_categories. Any other value matches nothing.",
        ),
    ] = None,
    recipient: Annotated[
        str | None,
        Query(
            description="One of the values in filter_vocabulary.recipients from "
            "get_categories. Use 'anyone' only if the shopper genuinely has nobody "
            "specific in mind - most products are tagged 'anyone', so it rarely narrows "
            "anything usefully.",
        ),
    ] = None,
    max_price_eur: Annotated[
        float | None,
        Query(
            gt=0,
            description="Upper bound in euros, inclusive. Treat a hard limit as hard: "
            "'under 70', 'max 70', 'no more than 70' means pass exactly 70 and never "
            "recommend above it. Only vague phrasing - 'around 50', 'about 50', 'or so' "
            "- earns headroom, and then pass about 20% more. If the only good option is "
            "over a hard limit, say the price plainly and let the shopper decide; do not "
            "quietly widen the budget on their behalf.",
        ),
    ] = None,
    min_price_eur: Annotated[
        float | None,
        Query(
            ge=0,
            description="Lower bound in euros, inclusive. Use only when the shopper wants "
            "something that feels substantial; otherwise omit it.",
        ),
    ] = None,
    include_out_of_stock: Annotated[
        bool,
        Query(
            description="Leave false. Recommending something unavailable wastes the "
            "shopper's time. Set true only when they ask specifically about an item you "
            "already know is out of stock.",
        ),
    ] = False,
    sort: Annotated[
        Literal["relevance", "price_asc", "price_desc", "rating"],
        Query(
            description="Keep 'relevance' unless the shopper asks for the cheapest, the "
            "most expensive, or the best reviewed.",
        ),
    ] = "relevance",
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=search.MAX_SEARCH_LIMIT,
            description="How many products to return. Ask for 5 even though you will show "
            "two or three: the extra give you room to pick the best fit and to hold a "
            "backup ready for when the shopper rejects one.",
        ),
    ] = search.DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    return search.enforce_budget(
        search.search_products(
            get_catalog(),
            query=query,
            category=category,
            occasion=occasion,
            recipient=recipient,
            max_price_eur=max_price_eur,
            min_price_eur=min_price_eur,
            include_out_of_stock=include_out_of_stock,
            sort=sort,
            limit=limit,
            base_url=_settings.public_base_url,
        )
    )


BY_CATEGORY_DESCRIPTION = """
For browsing - when the shopper has named a category and given you nothing else
("what have you got in Kitchen & Dining?"). If they have mentioned a budget, an
occasion, a recipient, or anything about what they want, use `search_products`
instead: it filters and ranks, this does not.

`category` accepts the display name or the slug and tolerates minor misspellings.
If it does not exist, `status` is `"unknown_category"` and the response lists the
real ones - use those rather than guessing again.

Returns 8 products per page. `total_matches` and `has_more` tell you whether more
exist, but prefer narrowing with `search_products` over paging through a long list:
the shopper wants two good options, not forty.
""".strip()


@tools.get(
    "/products/by-category",
    operation_id="get_products_by_category",
    summary="List the products in one category, most popular first, with pagination.",
    description=BY_CATEGORY_DESCRIPTION,
    response_model=ProductListResponse,
    response_model_exclude_none=True,
)
async def get_products_by_category(
    category: Annotated[
        str,
        Query(
            description="Category display name ('Home & Living') or slug ('home-living'), "
            "from get_categories. Minor misspellings are tolerated.",
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=search.MAX_CATEGORY_LIMIT, description="Products per page."),
    ] = search.DEFAULT_CATEGORY_LIMIT,
    offset: Annotated[
        int,
        Query(
            ge=0,
            description="How many products to skip. Use only if the shopper explicitly "
            "asks to see more of the same category.",
        ),
    ] = 0,
    sort: Annotated[
        Literal["popularity", "price_asc", "price_desc", "rating"],
        Query(description="Default 'popularity' - best-rated and most-reviewed first."),
    ] = "popularity",
    max_price_eur: Annotated[
        float | None,
        Query(gt=0, description="Upper bound in euros, inclusive."),
    ] = None,
    include_out_of_stock: Annotated[
        bool, Query(description="Leave false unless the shopper asks about an unavailable item.")
    ] = False,
) -> dict[str, Any]:
    return search.enforce_budget(
        search.products_by_category(
            get_catalog(),
            category=category,
            limit=limit,
            offset=offset,
            sort=sort,
            max_price_eur=max_price_eur,
            include_out_of_stock=include_out_of_stock,
            base_url=_settings.public_base_url,
        )
    )


DETAILS_DESCRIPTION = """
Returns everything in the summary plus brand, colour, material, the occasions it
suits, who it is for, its tags, and the exact stock count.

Call it when the shopper asks about a specific product you have already
recommended, or when you need a detail you do not have - material, colour, brand -
to answer a question or justify a recommendation. Do not call it for every search
result: the fields returned by `search_products` are enough to recommend from.

If the product is out of stock, the response also carries up to two in-stock
`alternatives`, so you can offer a replacement in the same reply instead of going
back to the shopper empty-handed.
""".strip()


@tools.get(
    "/products/{product_id}",
    operation_id="get_product_details",
    summary="Get the full record for one product, including material, colour, brand and exact stock.",
    description=DETAILS_DESCRIPTION,
    response_model=ProductDetailResponse,
    response_model_exclude_none=True,
)
async def get_product_details(
    product_id: Annotated[
        str,
        Path(
            description="Product identifier as returned by the other tools, e.g. 'HL-003'. "
            "Case-insensitive. If you have a product name rather than an id, call "
            "search_products first.",
        ),
    ],
) -> dict[str, Any]:
    return search.get_product_details(
        get_catalog(), product_id, base_url=_settings.public_base_url
    )


SIMILAR_DESCRIPTION = """
Two situations call for this. First, the shopper likes something they cannot
afford: pass `max_price_eur` set to their budget and you get the closest thing in
range. Second, what they want is out of stock: call it without a price limit to
offer a replacement.

Returns up to `limit` products, never including the original. If nothing similar
exists inside the constraints, `status` is `"no_match"` and the response explains
why - tell the shopper honestly rather than quietly widening the search.
""".strip()


@tools.get(
    "/products/{product_id}/similar",
    operation_id="find_similar_products",
    summary="Find in-stock products similar to a given one - same kind of thing, comparable style, close in price.",
    description=SIMILAR_DESCRIPTION,
    response_model=ProductListResponse,
    response_model_exclude_none=True,
)
async def find_similar_products(
    product_id: Annotated[
        str, Path(description="Identifier of the product to find alternatives to, e.g. 'TG-022'.")
    ],
    max_price_eur: Annotated[
        float | None,
        Query(
            gt=0,
            description="Set this to the shopper's budget when the original was too "
            "expensive. Omit it when you are replacing something that is out of stock.",
        ),
    ] = None,
    include_out_of_stock: Annotated[
        bool,
        Query(description="Leave false - the point of this tool is to find something buyable."),
    ] = False,
    limit: Annotated[
        int,
        Query(ge=1, le=search.MAX_SIMILAR_LIMIT, description="How many alternatives to return."),
    ] = search.DEFAULT_SIMILAR_LIMIT,
) -> dict[str, Any]:
    return search.enforce_budget(
        search.find_similar_products(
            get_catalog(),
            product_id=product_id,
            max_price_eur=max_price_eur,
            include_out_of_stock=include_out_of_stock,
            limit=limit,
            base_url=_settings.public_base_url,
        )
    )


app.include_router(tools)
