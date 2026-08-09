"""Behaviour of the five operations against the real catalogue.

These are the conversations the brief asks the agent to survive, tested at the
tool layer where the guarantees actually live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import search
from app.ingest import load_catalog

CSV = Path(__file__).resolve().parents[1] / "data" / "gift-shop-catalog.csv"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CSV, None)


# --- get_categories ------------------------------------------------------------


def test_categories_are_canonical(catalog):
    result = search.get_categories(catalog)
    names = [c["name"] for c in result["categories"]]
    assert len(names) == 11
    assert "Home & Living" in names
    assert not any(n.strip() != n or n.islower() for n in names)


def test_categories_carry_the_filter_vocabulary(catalog):
    """The model cannot guess these strings, so the bootstrap call must supply them."""
    vocabulary = search.get_categories(catalog)["filter_vocabulary"]
    assert set(vocabulary["recipients"]) == {"anyone", "him", "her", "couple", "kids"}
    assert "housewarming" in vocabulary["occasions"]


def test_category_price_ranges_let_the_agent_judge_budget(catalog):
    jewellery = next(
        c for c in search.get_categories(catalog)["categories"] if c["name"] == "Jewellery"
    )
    assert jewellery["price_range_eur"]["min"] == 54.0


# --- search_products -----------------------------------------------------------


def test_specific_request_returns_products(catalog):
    result = search.search_products(catalog, query="chef knife", max_price_eur=100)
    assert result["status"] == "ok"
    assert result["products"]
    assert all(p["price_eur"] <= 100 for p in result["products"])


def test_out_of_stock_is_excluded_by_default(catalog):
    result = search.search_products(catalog, category="Home & Living", limit=12)
    assert all(p["stock_level"] != "out_of_stock" for p in result["products"])


def test_out_of_stock_can_be_requested_explicitly(catalog):
    result = search.search_products(
        catalog, category="Home & Living", include_out_of_stock=True, limit=12
    )
    assert result["filters_applied"]["include_out_of_stock"] is True


def test_budget_too_low_names_the_real_floor(catalog):
    result = search.search_products(catalog, category="Jewellery", max_price_eur=30)
    assert result["status"] == "no_match"
    assert result["reason"] == "budget_too_low"
    assert "54" in result["message"] or "68" in result["message"]
    assert result["suggestions"]["relax"]


def test_budget_fallback_is_actually_buyable(catalog):
    """Regression: the cheapest match was once returned even when out of stock.

    Offering something unavailable as the fallback is the exact failure the whole
    stock-aware design exists to prevent.
    """
    result = search.search_products(catalog, category="Jewellery", max_price_eur=30)
    cheapest = result["suggestions"]["cheapest_in_scope"]
    assert cheapest["stock_level"] != "out_of_stock"


def test_nothing_suitable_is_said_plainly(catalog):
    result = search.search_products(catalog, query="scuba diving regulator")
    assert result["status"] == "no_match"
    assert result["reason"] == "no_query_match"
    assert result["products"] == []
    assert result["suggestions"]["relax"]


def test_unknown_category_suggests_a_real_one(catalog):
    result = search.search_products(catalog, category="homeware")
    # 'homeware' is close enough to resolve outright; a wilder guess must not.
    assert result["status"] in {"ok", "unknown_category"}

    result = search.search_products(catalog, category="Sorcery Supplies")
    assert result["status"] == "unknown_category"
    assert result["available_categories"]
    assert len(result["available_categories"]) == 11


def test_cross_listed_products_appear_once(catalog):
    result = search.search_products(catalog, query="herb garden kit", limit=12)
    names = [p["name"] for p in result["products"]]
    assert names.count("Herb Garden Kit") == 1


def test_limit_is_clamped_server_side(catalog):
    result = search.search_products(catalog, limit=999)
    assert result["returned"] <= search.MAX_SEARCH_LIMIT
    assert result["filters_applied"]["limit"] == search.MAX_SEARCH_LIMIT


def test_total_matches_survives_paging(catalog):
    result = search.search_products(catalog, category="Home & Living", limit=2)
    assert result["returned"] == 2
    assert result["total_matches"] > 2
    assert result["has_more"] is True
    assert result["notes"]


def test_filters_applied_echoes_the_resolved_category(catalog):
    result = search.search_products(catalog, category="home-living")
    assert result["filters_applied"]["category"] == "Home & Living"


# --- get_products_by_category --------------------------------------------------


def test_by_category_paginates(catalog):
    first = search.products_by_category(catalog, category="Home & Living", limit=5)
    second = search.products_by_category(catalog, category="Home & Living", limit=5, offset=5)
    assert first["products"] and second["products"]
    assert {p["product_id"] for p in first["products"]}.isdisjoint(
        {p["product_id"] for p in second["products"]}
    )


def test_by_category_accepts_slug_and_name(catalog):
    by_name = search.products_by_category(catalog, category="Kitchen & Dining")
    by_slug = search.products_by_category(catalog, category="kitchen-dining")
    assert by_name["total_matches"] == by_slug["total_matches"]


# --- get_product_details -------------------------------------------------------


def test_details_include_full_record(catalog):
    result = search.get_product_details(catalog, "HL-003")
    assert result["status"] == "ok"
    assert result["product"]["brand"] == "Fold Studio"
    assert result["product"]["material"]


def test_details_omit_unknown_fields_entirely(catalog):
    """Absent, never null - a null rating gets reported as 'rated 0 out of 5'."""
    product = search.get_product_details(catalog, "EX-001")["product"]
    assert "rating" not in product
    assert "material" not in product


def test_out_of_stock_detail_carries_alternatives(catalog):
    result = search.get_product_details(catalog, "TG-022")
    assert result["product"]["stock_level"] == "out_of_stock"
    assert result["alternatives"]
    assert all(a["stock_level"] != "out_of_stock" for a in result["alternatives"])
    assert "out of stock" in result["message"]


def test_unknown_product_is_recoverable(catalog):
    result = search.get_product_details(catalog, "ZZ-999")
    assert result["status"] == "not_found"
    assert result["notes"]


def test_product_id_lookup_is_case_insensitive(catalog):
    assert search.get_product_details(catalog, "hl-003")["status"] == "ok"


def test_cross_listed_detail_names_the_other_category(catalog):
    product = search.get_product_details(catalog, "HL-021")["product"]
    assert product["also_in_categories"] == ["Kitchen & Dining"]


# --- find_similar_products -----------------------------------------------------


def test_similar_respects_a_budget(catalog):
    result = search.find_similar_products(catalog, product_id="TG-022", max_price_eur=150)
    assert result["status"] == "ok"
    assert all(p["price_eur"] <= 150 for p in result["products"])


def test_similar_never_returns_the_original_or_its_twin(catalog):
    result = search.find_similar_products(catalog, product_id="HL-021", limit=6)
    ids = {p["product_id"] for p in result["products"]}
    assert "HL-021" not in ids
    assert "KD-024" not in ids          # the same product cross-listed


def test_similar_only_offers_buyable_products(catalog):
    result = search.find_similar_products(catalog, product_id="TG-022")
    assert all(p["stock_level"] != "out_of_stock" for p in result["products"])


def test_similar_for_unknown_id_is_recoverable(catalog):
    result = search.find_similar_products(catalog, product_id="ZZ-999")
    assert result["status"] == "not_found"
    assert result["products"] == []


# --- response budget -----------------------------------------------------------


def test_budget_leaves_normal_responses_untouched(catalog):
    result = search.search_products(catalog, limit=12)
    assert search.enforce_budget(dict(result)) == result


def test_budget_degrades_in_order_and_says_so(catalog):
    payload = search.search_products(catalog, limit=12)
    trimmed = search.enforce_budget(payload, max_bytes=900)

    assert len(str(trimmed).encode()) < len(str(payload).encode()) or trimmed["returned"] < 12
    assert any("trimmed" in note for note in trimmed["notes"])
    assert trimmed["returned"] == len(trimmed["products"])
    assert trimmed["products"], "never degrade below one product"


def test_budget_never_hides_how_much_was_missed(catalog):
    payload = search.search_products(catalog, limit=12)
    before = payload["total_matches"]
    assert search.enforce_budget(payload, max_bytes=600)["total_matches"] == before
