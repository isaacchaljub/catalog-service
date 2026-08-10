"""Response models.

Every `description` here is copied verbatim into the OpenAPI spec, which is what
indigo.ai pastes in to create the tools. The model reading it has nothing else to
go on, so these are written for that reader rather than for a human browsing docs.

Every optional field is genuinely optional: routes are declared with
`response_model_exclude_none=True`, so a field we do not know is *absent* rather
than null. A model handed `"rating": null` will happily tell a shopper the product
is rated zero out of five.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

StockLevel = Literal["in_stock", "low", "out_of_stock", "unknown"]
SearchSort = Literal["relevance", "price_asc", "price_desc", "rating"]
CategorySort = Literal["popularity", "price_asc", "price_desc", "rating"]
ResponseStatus = Literal[
    "ok", "no_match", "unknown_category", "not_found", "invalid_parameter"
]


class ProductSummary(BaseModel):
    """A product as it appears in a list of results - enough to recommend from."""

    product_id: str = Field(
        description="Stable identifier, e.g. 'HL-003'. Pass this to get_product_details "
        "or find_similar_products."
    )
    name: str = Field(description="Product name from the catalogue, in English.")
    price_eur: float = Field(description="Price in euros.")
    pitch: str | None = Field(
        default=None,
        description="One-line description of the product, at most 140 characters. "
        "Use it as the basis for why you are recommending this, in your own words.",
    )
    stock_level: StockLevel = Field(
        description="'in_stock' or 'low' can be recommended; 'low' means few remain. "
        "'out_of_stock' cannot be bought right now. 'unknown' means the catalogue "
        "export did not say - do not claim it is either available or sold out. Only "
        "mention this to the shopper when it is not 'in_stock' - 'low' is worth flagging "
        "as scarcity, 'unknown' needs the caveat above. A plain 'in_stock' needs no "
        "comment; stating it reads like reciting a database field."
    )
    shipping_days: int | None = Field(
        default=None,
        description="Working days to delivery. 0 means digital and delivered immediately.",
    )
    gift_wrap: bool | None = Field(
        default=None, description="Whether gift wrapping is offered for this product."
    )
    rating: float | None = Field(
        default=None,
        description="Average customer rating out of 5. Absent when the product has "
        "not been rated - absent is not the same as zero.",
    )
    reviews_count: int | None = Field(
        default=None,
        description="How many reviews the rating is based on. A high rating from many "
        "reviews is worth mentioning; from three reviews it is not.",
    )
    product_url: str | None = Field(
        default=None,
        description="Link to this product's page. Make the product name a markdown "
        "link to the exact string in this field, kept on the same line as the price - "
        "see your instructions for a worked example. Never read the link aloud.",
    )
    image_url: str | None = Field(
        default=None,
        description="Illustrative image for this product - a generated placeholder, not "
        "a photo. Embed it in your reply as a markdown image, ![name](url), for each "
        "product you recommend; do not describe it in words or claim it is a real photo.",
    )


class ProductDetail(ProductSummary):
    """Everything known about one product."""

    brand: str | None = Field(default=None, description="Brand name.")
    color: str | None = Field(default=None, description="Primary colour.")
    material: str | None = Field(default=None, description="What it is made of.")
    occasions: list[str] | None = Field(
        default=None, description="Occasions this product suits."
    )
    recipient: str | None = Field(
        default=None,
        description="Who the catalogue tags this for. 'anyone' means no particular "
        "recipient, not that it suits everyone equally.",
    )
    tags: list[str] | None = Field(
        default=None, description="Descriptive keywords, useful for explaining the fit."
    )
    stock: int | None = Field(
        default=None, description="Exact units in stock. Absent when the export did not say."
    )
    description: str | None = Field(
        default=None, description="Full product description, longer than the pitch."
    )
    also_in_categories: list[str] | None = Field(
        default=None,
        description="Other categories this same product is listed under. Present only "
        "for cross-listed products; it is one product, not several.",
    )


class PriceRange(BaseModel):
    min: float = Field(description="Cheapest product in this category, in euros.")
    max: float = Field(description="Most expensive product in this category, in euros.")


class CategoryInfo(BaseModel):
    name: str = Field(
        description="Display name. Pass this exact string as the `category` parameter."
    )
    slug: str = Field(
        description="URL-safe form, e.g. 'home-living'. Also accepted as `category`."
    )
    product_count: int = Field(description="Products in this category.")
    in_stock_count: int = Field(description="How many of those can be bought right now.")
    price_range_eur: PriceRange = Field(
        description="Use this to tell a shopper immediately whether their budget fits."
    )
    subcategories: list[str] = Field(
        default_factory=list, description="Narrower groupings inside this category."
    )


class FilterVocabulary(BaseModel):
    """The only values the corresponding filters accept. Derived from the catalogue."""

    occasions: list[str] = Field(
        description="Accepted values for the `occasion` parameter. Anything else matches nothing."
    )
    recipients: list[str] = Field(
        description="Accepted values for the `recipient` parameter. Anything else matches nothing."
    )


class CategoriesResponse(BaseModel):
    status: ResponseStatus = Field(description="Always 'ok' for this operation.")
    categories: list[CategoryInfo]
    filter_vocabulary: FilterVocabulary
    notes: list[str] | None = Field(
        default=None, description="Guidance for your next call. Read it before searching."
    )


class Suggestions(BaseModel):
    """What to do instead, when a search comes back empty."""

    cheapest_in_scope: ProductSummary | None = Field(
        default=None,
        description="The cheapest product matching every filter except the budget. "
        "Present when the budget was the reason nothing matched - offer it, or use "
        "its price to tell the shopper what the realistic minimum is.",
    )
    out_of_stock_matches: list[ProductSummary] | None = Field(
        default=None,
        description="Products that matched but cannot be bought right now. Present when "
        "stock was the only reason nothing came back. Mention them and offer alternatives "
        "via find_similar_products rather than pretending they do not exist.",
    )
    relax: list[str] | None = Field(
        default=None,
        description="Concrete changes that would produce results, e.g. 'raise "
        "max_price_eur to 54'. Act on one of these rather than reporting failure.",
    )


class ProductListResponse(BaseModel):
    """The shape returned by every operation that can produce more than one product."""

    status: ResponseStatus = Field(
        description="'ok' means `products` is usable. Any other value means nothing was "
        "returned and the rest of this response explains what to do instead."
    )
    message: str | None = Field(
        default=None,
        description="Plain-language explanation, written to be relayed to the shopper. "
        "Present whenever status is not 'ok'.",
    )
    reason: str | None = Field(
        default=None,
        description="Machine-readable cause when status is 'no_match': 'budget_too_low', "
        "'out_of_stock_only', 'no_query_match' or 'over_constrained'.",
    )
    total_matches: int = Field(
        description="How many products matched in total, which may exceed the number "
        "returned. Use it to judge whether to narrow the search rather than paginate."
    )
    returned: int = Field(description="How many products are in `products`.")
    filters_applied: dict[str, Any] = Field(
        default_factory=dict,
        description="The filters actually used, including defaults and any corrected "
        "category spelling. Check it before telling the shopper what you searched for.",
    )
    products: list[ProductSummary] = Field(
        default_factory=list,
        description="Products you can recommend right now. Out-of-stock products never "
        "appear here unless you asked for them.",
    )
    has_more: bool | None = Field(
        default=None, description="Whether more results exist beyond this page."
    )
    did_you_mean: list[str] | None = Field(
        default=None,
        description="Closest valid values when a parameter did not match anything. "
        "Retry with one of these rather than guessing again.",
    )
    available_categories: list[str] | None = Field(
        default=None, description="Every valid category, returned when an unknown one was requested."
    )
    suggestions: Suggestions | None = Field(
        default=None, description="What to try instead when nothing matched."
    )
    notes: list[str] | None = Field(
        default=None,
        description="Short instructions about this result: how much you did not see, "
        "what was hidden, how to narrow. Read them before replying.",
    )


class ProductDetailResponse(BaseModel):
    status: ResponseStatus = Field(
        description="'ok' means `product` is present. 'not_found' means the id does not exist."
    )
    message: str | None = Field(
        default=None, description="Explanation when status is not 'ok'."
    )
    product: ProductDetail | None = None
    alternatives: list[ProductSummary] | None = Field(
        default=None,
        description="In-stock products similar to this one. Returned automatically when "
        "the requested product is out of stock, so you can offer a replacement in the "
        "same reply.",
    )
    did_you_mean: list[str] | None = Field(
        default=None, description="Product ids with similar names, when the id was not found."
    )
    notes: list[str] | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    catalog: dict[str, Any] = Field(
        description="Data-quality summary from the last catalogue load."
    )


class AuthError(BaseModel):
    detail: str
