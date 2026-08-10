"""Filtering, ranking and response shaping.

Pure functions over an in-memory `Catalog`. The REST endpoints and the MCP server
both call these, so there is one implementation of "what does the agent get back"
rather than two that drift.

Ranking is deliberately a transparent weighted sum rather than an embedding model.
With 152 products a vector index would add a dependency, a cold-start cost and a
ranking nobody can explain in a review. At ~50k products the trade would flip.
"""

from __future__ import annotations

import json
from typing import Any

from app import errors
from app.ingest import Catalog, Product, category_key, slugify, tokenize

# --- Limits --------------------------------------------------------------------

DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT = 5, 12
DEFAULT_CATEGORY_LIMIT, MAX_CATEGORY_LIMIT = 8, 20
DEFAULT_SIMILAR_LIMIT, MAX_SIMILAR_LIMIT = 3, 6

MAX_RESPONSE_BYTES = 12_288          # ~3k tokens at ~4 bytes per token
BUDGET_PITCH_CHARS = 90

# 'unknown' is recommendable: the export not saying how many are left is not the
# same as knowing there are none.
RECOMMENDABLE = {"in_stock", "low", "unknown"}

SEARCH_SORTS = ["relevance", "price_asc", "price_desc", "rating"]
CATEGORY_SORTS = ["popularity", "price_asc", "price_desc", "rating"]


# --- Projection ----------------------------------------------------------------


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown values. Absent is unambiguous; null invites invention."""
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != [] and value != ""
    }


def to_summary(product: Product, *, base_url: str | None = None) -> dict[str, Any]:
    return _compact(
        {
            "product_id": product.product_id,
            "name": product.name,
            "price_eur": product.price_eur,
            "pitch": product.pitch,
            "stock_level": product.stock_level,
            "shipping_days": product.shipping_days,
            "gift_wrap": product.gift_wrap,
            "rating": product.rating,
            "reviews_count": product.reviews_count,
            "product_url": f"{base_url}/p/{product.product_id}" if base_url else None,
        }
    )


def to_detail(product: Product, *, base_url: str | None = None) -> dict[str, Any]:
    detail = to_summary(product, base_url=base_url)
    detail.update(
        _compact(
            {
                "brand": product.brand,
                "color": product.color,
                "material": product.material,
                "occasions": product.occasions,
                "recipient": product.recipient,
                "tags": product.tags,
                "stock": product.stock,
                "description": product.description,
                "also_in_categories": product.also_in_categories,
            }
        )
    )
    return detail


# --- Category resolution -------------------------------------------------------


def resolve_category(catalog: Catalog, requested: str) -> str | None:
    """Accept a display name, a slug, or a near-miss spelling. None means unknown."""
    if not requested or not requested.strip():
        return None

    key = category_key(requested)
    members = catalog.by_category_key.get(key)
    if members:
        return members[0].category

    display = catalog.category_slugs.get(slugify(requested))
    if display:
        return display

    suggestions = errors.suggest_categories(catalog, requested)
    return suggestions[0] if suggestions else None


# --- Ranking -------------------------------------------------------------------


def score_product(
    product: Product,
    query_tokens: set[str],
    *,
    occasion: str | None,
    recipient: str | None,
    max_price_eur: float | None,
    max_popularity: float,
) -> float:
    score = 0.0
    if query_tokens:
        score += 3.0 * len(query_tokens & product.name_tokens)
        score += 2.0 * len(query_tokens & product.tag_tokens)
        score += 1.0 * len(query_tokens & product.tokens)

    if occasion and occasion in product.occasions:
        score += 2.0
    # An exact recipient match is a real signal; matching the catch-all 'anyone' is not.
    if recipient and product.recipient == recipient and recipient != "anyone":
        score += 1.5

    if max_price_eur:
        ratio = product.price_eur / max_price_eur
        if 0.7 <= ratio <= 1.0:
            score += 1.0          # uses the budget well
        elif 0.4 <= ratio < 0.7:
            score += 0.5

    if product.stock_level == "low":
        score -= 1.0
    if max_popularity > 0:
        score += 0.5 * (product.popularity / max_popularity)
    return score


def _sorted(products: list[Product], sort: str, scores: dict[str, float]) -> list[Product]:
    if sort == "price_asc":
        return sorted(products, key=lambda p: (p.price_eur, p.product_id))
    if sort == "price_desc":
        return sorted(products, key=lambda p: (-p.price_eur, p.product_id))
    if sort == "rating":
        return sorted(products, key=lambda p: (-(p.rating or -1), -(p.reviews_count or 0), p.product_id))
    if sort == "popularity":
        return sorted(products, key=lambda p: (-p.popularity, p.product_id))
    return sorted(products, key=lambda p: (-scores.get(p.product_id, 0.0), -p.popularity, p.product_id))


def _dedupe(products: list[Product]) -> tuple[list[Product], int]:
    """Collapse cross-listed products so one thing never appears twice in a reply.

    The list is already ordered best-first, so keeping the first member of each
    duplicate group keeps the one that ranked highest for this particular query.
    """
    seen_groups: set[str] = set()
    kept: list[Product] = []
    removed = 0
    for product in products:
        group = product.duplicate_group_id
        if group and group in seen_groups:
            removed += 1
            continue
        if group:
            seen_groups.add(group)
        kept.append(product)
    return kept, removed


# --- Operations ----------------------------------------------------------------


def get_categories(catalog: Catalog) -> dict[str, Any]:
    categories = []
    for name in catalog.categories:
        members = [p for p in catalog.products if p.category == name]
        prices = [p.price_eur for p in members]
        categories.append(
            {
                "name": name,
                "slug": slugify(name),
                "product_count": len(members),
                "in_stock_count": sum(1 for p in members if p.stock_level in RECOMMENDABLE),
                "price_range_eur": {"min": min(prices), "max": max(prices)},
                "subcategories": sorted({p.subcategory for p in members if p.subcategory}),
            }
        )

    return {
        "status": "ok",
        "categories": categories,
        "filter_vocabulary": {
            "occasions": catalog.occasions,
            "recipients": catalog.recipients,
        },
        "notes": [
            "Pass these exact strings as the category, occasion and recipient "
            "parameters of search_products. Values not listed here match nothing.",
            "This response describes the catalogue, not products. "
            "Call search_products to get something you can recommend.",
        ],
    }


def search_products(
    catalog: Catalog,
    *,
    query: str | None = None,
    category: str | None = None,
    occasion: str | None = None,
    recipient: str | None = None,
    max_price_eur: float | None = None,
    min_price_eur: float | None = None,
    include_out_of_stock: bool = False,
    sort: str = "relevance",
    limit: int = DEFAULT_SEARCH_LIMIT,
    base_url: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))

    filters: dict[str, Any] = _compact(
        {
            "query": query,
            "occasion": occasion,
            "recipient": recipient,
            "max_price_eur": max_price_eur,
            "min_price_eur": min_price_eur,
        }
    )
    filters["include_out_of_stock"] = include_out_of_stock
    filters["sort"] = sort
    filters["limit"] = limit

    resolved_category = None
    if category:
        resolved_category = resolve_category(catalog, category)
        if resolved_category is None:
            return errors.unknown_category(catalog, category, filters)
        filters["category"] = resolved_category

    query_tokens = tokenize(query) if query else set()

    # Stage the filters so an empty result can explain *which* one emptied it.
    by_attributes = [
        p
        for p in catalog.products
        if (not resolved_category or p.category == resolved_category)
        and (not occasion or occasion in p.occasions)
        and (not recipient or p.recipient == recipient)
        and (not query_tokens or (query_tokens & p.tokens))
    ]
    by_price = [
        p
        for p in by_attributes
        if (max_price_eur is None or p.price_eur <= max_price_eur)
        and (min_price_eur is None or p.price_eur >= min_price_eur)
    ]
    available = (
        by_price
        if include_out_of_stock
        else [p for p in by_price if p.stock_level in RECOMMENDABLE]
    )

    if not available:
        return _no_match(
            catalog,
            filters=filters,
            by_attributes=by_attributes,
            by_price=by_price,
            price_filtered=max_price_eur is not None or min_price_eur is not None,
            had_query=bool(query_tokens),
            include_out_of_stock=include_out_of_stock,
            base_url=base_url,
        )

    max_popularity = max((p.popularity for p in catalog.products), default=0.0)
    scores = {
        p.product_id: score_product(
            p,
            query_tokens,
            occasion=occasion,
            recipient=recipient,
            max_price_eur=max_price_eur,
            max_popularity=max_popularity,
        )
        for p in available
    }
    ordered, _ = _dedupe(_sorted(available, sort, scores))
    page = ordered[:limit]

    notes: list[str] = []
    hidden = len(by_price) - len(available)
    if len(ordered) > limit:
        notes.append(
            f"{len(ordered) - limit} more products match. Narrow with occasion, "
            f"recipient or a tighter budget rather than asking for a longer list."
        )
    if hidden:
        notes.append(f"{hidden} matching product(s) hidden because they are out of stock.")

    return {
        "status": "ok",
        "total_matches": len(ordered),
        "returned": len(page),
        "filters_applied": filters,
        "products": [to_summary(p, base_url=base_url) for p in page],
        "has_more": len(ordered) > limit,
        "notes": notes or None,
    }


def _no_match(
    catalog: Catalog,
    *,
    filters: dict[str, Any],
    by_attributes: list[Product],
    by_price: list[Product],
    price_filtered: bool,
    had_query: bool,
    include_out_of_stock: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Explain what emptied the result and hand back something actionable."""
    # Anything offered as a fallback must itself be buyable. Suggesting the cheapest
    # match and having it be out of stock is the exact failure this design exists to
    # prevent.
    if not include_out_of_stock:
        by_attributes = [p for p in by_attributes if p.stock_level in RECOMMENDABLE]

    base = {
        "status": "no_match",
        "total_matches": 0,
        "returned": 0,
        "filters_applied": filters,
        "products": [],
    }

    if by_price:
        # Everything inside the budget matched, but none of it is available. There may
        # still be something buyable just above the budget, and "it is 2 EUR over" is a
        # far more actionable answer than "everything is sold out", so say both.
        shown = sorted(by_price, key=lambda p: -p.popularity)[:2]
        within_budget = {p.product_id for p in by_price}
        over_budget = [p for p in by_attributes if p.product_id not in within_budget]
        suggestions: dict[str, Any] = {
            "out_of_stock_matches": [to_summary(p, base_url=base_url) for p in shown],
            "relax": [
                f"call find_similar_products with product_id "
                f"'{shown[0].product_id}' to offer an in-stock replacement"
            ],
        }
        message = f"{len(by_price)} product(s) match, but every one is currently out of stock."
        if price_filtered and over_budget:
            nearest = min(over_budget, key=lambda p: p.price_eur)
            suggestions["cheapest_in_scope"] = to_summary(nearest, base_url=base_url)
            suggestions["relax"].insert(
                0, f"raise max_price_eur to {nearest.price_eur:g} for {nearest.name}"
            )
            message += (
                f" {nearest.name} is in stock at {nearest.price_eur:g} EUR, just over the budget."
            )
        return base | {
            "reason": "out_of_stock_only",
            "message": message,
            "suggestions": suggestions,
            "notes": [
                "Tell the shopper plainly that these are unavailable, then offer an "
                "alternative. Do not recommend them as if they could be bought."
            ],
        }

    if by_attributes and price_filtered:
        cheapest = min(by_attributes, key=lambda p: p.price_eur)
        scope = filters.get("category", "the catalogue")
        relax = [f"raise max_price_eur to {cheapest.price_eur:g}"]
        buyable = [p for p in catalog.products if p.stock_level in RECOMMENDABLE]
        cheapest_overall = min(buyable or catalog.products, key=lambda p: p.price_eur)
        if cheapest_overall.category != cheapest.category:
            relax.append(
                f"try category '{cheapest_overall.category}', which starts at "
                f"{cheapest_overall.price_eur:g} EUR"
            )
        return base | {
            "reason": "budget_too_low",
            "message": (
                f"Nothing in {scope} fits that budget. "
                f"The cheapest match is {cheapest.name} at {cheapest.price_eur:g} EUR."
            ),
            "suggestions": {
                "cheapest_in_scope": to_summary(cheapest, base_url=base_url),
                "relax": relax,
            },
            "notes": [
                "Be honest about the gap rather than silently widening the search. "
                "Offer the cheapest match, or ask whether the budget can move."
            ],
        }

    if had_query:
        return base | {
            "reason": "no_query_match",
            "message": "Nothing in the catalogue matches that description.",
            "suggestions": {
                "relax": [
                    "retry with fewer or more general words",
                    "drop the query and filter by category, occasion and budget instead",
                ]
            },
            "notes": ["Call get_categories if you need to see what the catalogue covers."],
        }

    return base | {
        "reason": "over_constrained",
        "message": "No product matches all of those filters at once.",
        "suggestions": {"relax": ["remove the least important filter and search again"]},
        "notes": ["Prefer dropping occasion or recipient before widening the budget."],
    }


def products_by_category(
    catalog: Catalog,
    *,
    category: str,
    limit: int = DEFAULT_CATEGORY_LIMIT,
    offset: int = 0,
    sort: str = "popularity",
    max_price_eur: float | None = None,
    include_out_of_stock: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_CATEGORY_LIMIT))
    offset = max(0, offset)

    filters: dict[str, Any] = {
        "category": category,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "include_out_of_stock": include_out_of_stock,
    }
    if max_price_eur is not None:
        filters["max_price_eur"] = max_price_eur

    resolved = resolve_category(catalog, category)
    if resolved is None:
        return errors.unknown_category(catalog, category, filters)
    filters["category"] = resolved

    members = [p for p in catalog.products if p.category == resolved]
    if max_price_eur is not None:
        members = [p for p in members if p.price_eur <= max_price_eur]
    available = (
        members if include_out_of_stock else [p for p in members if p.stock_level in RECOMMENDABLE]
    )

    if not available:
        return _no_match(
            catalog,
            filters=filters,
            by_attributes=[p for p in catalog.products if p.category == resolved],
            by_price=members,
            price_filtered=max_price_eur is not None,
            had_query=False,
            include_out_of_stock=include_out_of_stock,
            base_url=base_url,
        )

    ordered, _ = _dedupe(_sorted(available, sort, {}))
    page = ordered[offset : offset + limit]

    notes: list[str] = []
    remaining = len(ordered) - (offset + len(page))
    if remaining > 0:
        notes.append(
            f"{remaining} more product(s) in {resolved}. Prefer narrowing with "
            f"search_products over paging - the shopper wants two good options, not a list."
        )
    hidden = len(members) - len(available)
    if hidden:
        notes.append(f"{hidden} product(s) hidden because they are out of stock.")

    return {
        "status": "ok",
        "total_matches": len(ordered),
        "returned": len(page),
        "filters_applied": filters,
        "products": [to_summary(p, base_url=base_url) for p in page],
        "has_more": remaining > 0,
        "notes": notes or None,
    }


def _similarity(candidate: Product, target: Product) -> float:
    score = 0.0
    if candidate.subcategory and candidate.subcategory == target.subcategory:
        score += 2.0
    if candidate.category == target.category:
        score += 1.0

    tags_a, tags_b = set(candidate.tags), set(target.tags)
    if tags_a or tags_b:
        score += len(tags_a & tags_b) / len(tags_a | tags_b)

    highest = max(candidate.price_eur, target.price_eur, 1.0)
    score += 1.0 - abs(candidate.price_eur - target.price_eur) / highest
    return score


def find_similar_products(
    catalog: Catalog,
    *,
    product_id: str,
    max_price_eur: float | None = None,
    include_out_of_stock: bool = False,
    limit: int = DEFAULT_SIMILAR_LIMIT,
    base_url: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_SIMILAR_LIMIT))
    filters: dict[str, Any] = {
        "product_id": product_id,
        "limit": limit,
        "include_out_of_stock": include_out_of_stock,
    }
    if max_price_eur is not None:
        filters["max_price_eur"] = max_price_eur

    target = catalog.get(product_id)
    if target is None:
        return errors.not_found(catalog, product_id) | {
            "total_matches": 0,
            "returned": 0,
            "filters_applied": filters,
            "products": [],
        }

    candidates = [
        p
        for p in catalog.products
        if p.product_id != target.product_id
        and not (p.duplicate_group_id and p.duplicate_group_id == target.duplicate_group_id)
    ]
    by_price = (
        candidates
        if max_price_eur is None
        else [p for p in candidates if p.price_eur <= max_price_eur]
    )
    available = (
        by_price if include_out_of_stock else [p for p in by_price if p.stock_level in RECOMMENDABLE]
    )

    if not available:
        result = _no_match(
            catalog,
            filters=filters,
            by_attributes=candidates,
            by_price=by_price,
            price_filtered=max_price_eur is not None,
            had_query=False,
            include_out_of_stock=include_out_of_stock,
            base_url=base_url,
        )
        result["message"] = (
            f"Nothing comparable to {target.name} is available"
            + (f" under {max_price_eur:g} EUR." if max_price_eur else ".")
        )
        return result

    ranked = sorted(
        available, key=lambda p: (-_similarity(p, target), -p.popularity, p.product_id)
    )
    ordered, _ = _dedupe(ranked)
    page = ordered[:limit]

    return {
        "status": "ok",
        "total_matches": len(ordered),
        "returned": len(page),
        "filters_applied": filters,
        "products": [to_summary(p, base_url=base_url) for p in page],
        "has_more": False,
        "notes": [
            f"Ranked by similarity to {target.name} ({target.price_eur:g} EUR). "
            f"Say what makes each one a comparable choice, not just that it is similar."
        ],
    }


def get_product_details(
    catalog: Catalog, product_id: str, *, base_url: str | None = None
) -> dict[str, Any]:
    product = catalog.get(product_id)
    if product is None:
        return errors.not_found(catalog, product_id)

    payload: dict[str, Any] = {"status": "ok", "product": to_detail(product, base_url=base_url)}

    if product.stock_level == "out_of_stock":
        alternatives = find_similar_products(
            catalog, product_id=product.product_id, limit=2, base_url=base_url
        )
        payload["alternatives"] = alternatives.get("products") or None
        payload["message"] = f"{product.name} is currently out of stock."
        payload["notes"] = [
            "Say it is unavailable before offering anything else, then suggest one of "
            "the alternatives. Do not recommend this product as if it could be bought."
        ]
    return _compact(payload)


# --- Response budget -----------------------------------------------------------


def _payload_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def enforce_budget(payload: dict[str, Any], max_bytes: int = MAX_RESPONSE_BYTES) -> dict[str, Any]:
    """Cap what one tool response can cost the agent's context window.

    This is a backstop, not the primary sizing mechanism - `limit` defaults, the
    summary/detail split and the 140-character pitch cap do that work first. It
    exists so a future export with very long text degrades instead of flooding.

    Degradation is ordered and always announced: a silently truncated result would
    have the agent recommending from data it never received.
    """
    if _payload_bytes(payload) <= max_bytes:
        return payload

    products: list[dict[str, Any]] = payload.get("products") or []
    trims: list[str] = []

    for product in products:
        pitch = product.get("pitch")
        if pitch and len(pitch) > BUDGET_PITCH_CHARS:
            product["pitch"] = pitch[: BUDGET_PITCH_CHARS - 1].rstrip() + "…"
            trims.append("descriptions shortened")

    if _payload_bytes(payload) > max_bytes:
        for product in products:
            for droppable in ("reviews_count", "gift_wrap"):
                product.pop(droppable, None)
        trims.append("some fields omitted")

    original = len(products)
    while _payload_bytes(payload) > max_bytes and len(products) > 1:
        products.pop()
    if len(products) < original:
        trims.append(f"{len(products)} of {original} products shown")

    if trims:
        payload["returned"] = len(products)
        notes = payload.get("notes") or []
        ordered_trims = list(dict.fromkeys(trims))
        notes.append("Response trimmed to fit the context budget: " + "; ".join(ordered_trims) + ".")
        payload["notes"] = notes
    return payload
