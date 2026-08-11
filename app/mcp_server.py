"""MCP transport in front of `app/search.py`.

Same operations as the REST surface in `app/main.py`, exposed as MCP tools instead
of HTTP routes so an MCP-speaking agent can call the catalogue directly. Every tool
is a thin adapter - parse arguments, call `app/search.py`, return its dict - so
there is exactly one implementation of ranking, filtering and response shaping.

Tool names, descriptions and parameter descriptions are copied verbatim from the
REST operations in `app/main.py` (and therefore from `openapi.json`): they are
tuned prompt surface, not documentation to be improved on independently.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from pydantic import Field
from starlette.middleware import Middleware

from app import auth, search
from app.config import Settings
from app.ingest import Catalog


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    """Match the REST twins' `response_model_exclude_none=True`.

    The REST handlers return the exact same dicts these tools do, but FastAPI
    strips `None` fields against the response model on the way out. The MCP
    transport has no such model, so without this a tool result can carry
    `"notes": null` where the REST response simply omits the key.
    """
    return {key: value for key, value in payload.items() if value is not None}


def build_mcp_server(
    get_catalog: Callable[[], Catalog],
    get_settings: Callable[[], Settings],
) -> FastMCP:
    mcp = FastMCP(name="gift-catalog")

    @mcp.tool(
        name="get_categories",
        description=(
            "List every category with counts, price ranges, and the exact filter "
            "values this catalogue uses."
        ),
    )
    def get_categories() -> dict[str, Any]:
        return _drop_none(search.enforce_budget(search.get_categories(get_catalog())))

    @mcp.tool(
        name="search_products",
        description=(
            "Find gift products matching a shopper's constraints - budget, "
            "occasion, recipient, category or free text."
        ),
    )
    def search_products(
        query: Annotated[
            str | None,
            Field(
                description="Free text describing what the shopper wants, in their own words "
                "where possible - 'cosy blanket for a new flat', 'chef's knife'. Matched "
                "against product names, tags, subcategories and descriptions. Leave empty if "
                "they have given only structured constraints such as a budget.",
            ),
        ] = None,
        category: Annotated[
            str | None,
            Field(
                description="Category display name ('Home & Living') or slug ('home-living'). "
                "Minor misspellings are tolerated. Use a value from get_categories.",
            ),
        ] = None,
        occasion: Annotated[
            str | None,
            Field(
                description="One of the values in filter_vocabulary.occasions from "
                "get_categories. Any other value matches nothing.",
            ),
        ] = None,
        recipient: Annotated[
            str | None,
            Field(
                description="One of the values in filter_vocabulary.recipients from "
                "get_categories. Use 'anyone' only if the shopper genuinely has nobody "
                "specific in mind - most products are tagged 'anyone', so it rarely narrows "
                "anything usefully.",
            ),
        ] = None,
        max_price_eur: Annotated[
            float | None,
            Field(
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
            Field(
                ge=0,
                description="Lower bound in euros, inclusive. Use only when the shopper wants "
                "something that feels substantial; otherwise omit it.",
            ),
        ] = None,
        include_out_of_stock: Annotated[
            bool,
            Field(
                description="Leave false. Recommending something unavailable wastes the "
                "shopper's time. Set true only when they ask specifically about an item you "
                "already know is out of stock.",
            ),
        ] = False,
        sort: Annotated[
            Literal["relevance", "price_asc", "price_desc", "rating"],
            Field(
                description="Keep 'relevance' unless the shopper asks for the cheapest, the "
                "most expensive, or the best reviewed.",
            ),
        ] = "relevance",
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=search.MAX_SEARCH_LIMIT,
                description="How many products to return. Ask for 5 even though you will show "
                "two or three: the extra give you room to pick the best fit and to hold a "
                "backup ready for when the shopper rejects one.",
            ),
        ] = search.DEFAULT_SEARCH_LIMIT,
    ) -> dict[str, Any]:
        return _drop_none(
            search.enforce_budget(
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
                    base_url=get_settings().public_base_url,
                )
            )
        )

    @mcp.tool(
        name="get_products_by_category",
        description="List the products in one category, most popular first, with pagination.",
    )
    def get_products_by_category(
        category: Annotated[
            str,
            Field(
                description="Category display name ('Home & Living') or slug ('home-living'), "
                "from get_categories. Minor misspellings are tolerated.",
            ),
        ],
        limit: Annotated[
            int,
            Field(ge=1, le=search.MAX_CATEGORY_LIMIT, description="Products per page."),
        ] = search.DEFAULT_CATEGORY_LIMIT,
        offset: Annotated[
            int,
            Field(
                ge=0,
                description="How many products to skip. Use only if the shopper explicitly "
                "asks to see more of the same category.",
            ),
        ] = 0,
        sort: Annotated[
            Literal["popularity", "price_asc", "price_desc", "rating"],
            Field(description="Default 'popularity' - best-rated and most-reviewed first."),
        ] = "popularity",
        max_price_eur: Annotated[
            float | None,
            Field(gt=0, description="Upper bound in euros, inclusive."),
        ] = None,
        include_out_of_stock: Annotated[
            bool, Field(description="Leave false unless the shopper asks about an unavailable item.")
        ] = False,
    ) -> dict[str, Any]:
        return _drop_none(
            search.enforce_budget(
                search.products_by_category(
                    get_catalog(),
                    category=category,
                    limit=limit,
                    offset=offset,
                    sort=sort,
                    max_price_eur=max_price_eur,
                    include_out_of_stock=include_out_of_stock,
                    base_url=get_settings().public_base_url,
                )
            )
        )

    @mcp.tool(
        name="get_product_details",
        description=(
            "Get the full record for one product, including material, colour, brand and "
            "exact stock."
        ),
    )
    def get_product_details(
        product_id: Annotated[
            str,
            Field(
                description="Product identifier as returned by the other tools, e.g. 'HL-003'. "
                "Case-insensitive. If you have a product name rather than an id, call "
                "search_products first.",
            ),
        ],
    ) -> dict[str, Any]:
        # No enforce_budget here, matching the REST twin - a single product cannot
        # blow the budget. `_drop_none` still applies, because the REST route sets
        # response_model_exclude_none=True and parity is the point of this module.
        return _drop_none(
            search.get_product_details(
                get_catalog(), product_id, base_url=get_settings().public_base_url
            )
        )

    @mcp.tool(
        name="find_similar_products",
        description=(
            "Find in-stock products similar to a given one - same kind of thing, comparable "
            "style, close in price."
        ),
    )
    def find_similar_products(
        product_id: Annotated[
            str, Field(description="Identifier of the product to find alternatives to, e.g. 'TG-022'.")
        ],
        max_price_eur: Annotated[
            float | None,
            Field(
                gt=0,
                description="Set this to the shopper's budget when the original was too "
                "expensive. Omit it when you are replacing something that is out of stock.",
            ),
        ] = None,
        include_out_of_stock: Annotated[
            bool,
            Field(description="Leave false - the point of this tool is to find something buyable."),
        ] = False,
        limit: Annotated[
            int,
            Field(ge=1, le=search.MAX_SIMILAR_LIMIT, description="How many alternatives to return."),
        ] = search.DEFAULT_SIMILAR_LIMIT,
    ) -> dict[str, Any]:
        return _drop_none(
            search.enforce_budget(
                search.find_similar_products(
                    get_catalog(),
                    product_id=product_id,
                    max_price_eur=max_price_eur,
                    include_out_of_stock=include_out_of_stock,
                    limit=limit,
                    base_url=get_settings().public_base_url,
                )
            )
        )

    return mcp


def build_mcp_app(
    get_catalog: Callable[[], Catalog],
    get_settings: Callable[[], Settings],
) -> StarletteWithLifespan:
    mcp = build_mcp_server(get_catalog, get_settings)
    return mcp.http_app(
        path="/mcp",
        # Cloud Run scales to zero and runs multiple instances with no session
        # affinity, so a stateful MCP session (assumed pinned to one instance)
        # would break across requests.
        stateless_http=True,
        # `tools`'s router-level `Depends(verify_bearer)` in app/main.py cannot see
        # this app once it is mounted - see BearerAuthASGIMiddleware.
        middleware=[Middleware(auth.BearerAuthASGIMiddleware)],
    )
