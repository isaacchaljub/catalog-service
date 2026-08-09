"""Recoverable responses.

Design decision, defended in the README: **401 for authentication is the only
non-2xx this service returns.** Every other condition an agent could act on comes
back 200 with a `status` field and a `message` written to be read aloud.

The reason is that we do not control how the consuming platform turns a non-2xx
response into the model's context. Several importers surface only "tool call
failed" and discard the body, which would throw away exactly the information the
agent needs to recover. A 404 that the model never sees is worse than a 200 it can
act on. Authentication is the exception because a missing token is a deployment
problem, not a conversation the agent can rescue.
"""

from __future__ import annotations

import difflib
from typing import Any

from app.ingest import Catalog

MAX_SUGGESTIONS = 3


def _closest(needle: str, haystack: list[str], limit: int = MAX_SUGGESTIONS) -> list[str]:
    """Fuzzy match, then substring match. Cheap, and it rescues most model guesses."""
    needle = needle.strip().casefold()
    if not needle:
        return []

    lowered = {value.casefold(): value for value in haystack}
    close = difflib.get_close_matches(needle, list(lowered), n=limit, cutoff=0.6)
    matches = [lowered[c] for c in close]

    # "homeware" is not a close match for "home & living" by edit distance, but a
    # shared prefix is a strong signal, so try containment too.
    for key, original in lowered.items():
        if len(matches) >= limit:
            break
        if original in matches:
            continue
        if needle in key or key in needle or key.split(" ")[0].startswith(needle[:4]):
            matches.append(original)
    return matches[:limit]


def suggest_categories(catalog: Catalog, requested: str) -> list[str]:
    candidates = catalog.categories + list(catalog.category_slugs)
    seen: list[str] = []
    for match in _closest(requested, candidates):
        display = catalog.category_slugs.get(match.casefold(), match)
        if display not in seen:
            seen.append(display)
    return seen


def suggest_product_ids(catalog: Catalog, requested: str) -> list[str]:
    by_name = {product.name: product.product_id for product in catalog.products}
    return [by_name[name] for name in _closest(requested, list(by_name))]


def unknown_category(
    catalog: Catalog, requested: str, filters: dict[str, Any]
) -> dict[str, Any]:
    suggestions = suggest_categories(catalog, requested)
    hint = f" Did you mean '{suggestions[0]}'?" if suggestions else ""
    return {
        "status": "unknown_category",
        "message": f"There is no category called '{requested}'.{hint}",
        "total_matches": 0,
        "returned": 0,
        "filters_applied": filters,
        "products": [],
        "did_you_mean": suggestions or None,
        "available_categories": catalog.categories,
        "notes": [
            "Retry with one of the categories listed in available_categories, "
            "or use search_products with a free-text query instead."
        ],
    }


def not_found(catalog: Catalog, product_id: str) -> dict[str, Any]:
    suggestions = suggest_product_ids(catalog, product_id)
    return {
        "status": "not_found",
        "message": f"No product with id '{product_id}' exists in this catalogue.",
        "did_you_mean": suggestions or None,
        "notes": [
            "Product ids look like 'HL-003'. If you are working from a product name "
            "rather than an id, call search_products to find the id first."
        ],
    }


def invalid_parameter(
    parameter: str, value: Any, allowed: list[str], filters: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "status": "invalid_parameter",
        "message": (
            f"'{value}' is not a valid value for {parameter}. "
            f"Allowed values: {', '.join(allowed)}."
        ),
        "total_matches": 0,
        "returned": 0,
        "filters_applied": filters or {},
        "products": [],
        "did_you_mean": _closest(str(value), allowed) or None,
        "notes": [f"Retry with one of: {', '.join(allowed)}."],
    }
