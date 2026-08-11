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
TRANSLATIONS = Path(__file__).resolve().parents[1] / "data" / "translations_es.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CSV, None)


@pytest.fixture(scope="module")
def catalog_es():
    """The catalogue as the deployed service loads it - translations included."""
    return load_catalog(CSV, None, TRANSLATIONS)


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


def test_query_matches_across_word_endings(catalog):
    """Regression: `bake` returned no_match while `baking` found the Bread Baking Set.

    Seen live - the agent searched "bake" for a shopper asking about repostería,
    got nothing back and apologised for a product we stock at 72 €.
    """
    for query in ("bake", "baking", "baked"):
        result = search.search_products(catalog, query=query, category="Kitchen & Dining")
        assert result["status"] == "ok", f"{query!r} found nothing"
        assert "Bread Baking Set" in [p["name"] for p in result["products"]]


def test_exact_word_still_outranks_a_stem_only_match(catalog):
    """Stemming widens the net; it must not reorder what was already correct."""
    result = search.search_products(catalog, query="knife", category="Kitchen & Dining")
    assert [p["name"] for p in result["products"]][:2] == [
        "Chef's Knife 20cm",
        "Paring Knife 9cm",
    ]


def test_stemming_only_merges_real_word_families(catalog):
    """The guard rail for `stem()`: unrelated words must not collapse together.

    Asserting "every word starts with its stem" would be vacuous - `stem` only
    ever strips from the end. So this pins named pairs in both directions, then
    checks the shape of the families the rules actually produce over the real
    catalogue. That is what lets the stemmer stay a few suffix rules rather than
    a dependency.
    """
    from app.ingest import stem

    same_family = [
        ("bake", "baking"), ("bake", "baked"), ("cook", "cooking"),
        ("candle", "candles"), ("knife", "knives"), ("glass", "glasses"),
        ("dish", "dishes"), ("run", "running"), ("box", "boxes"),
    ]
    for first, second in same_family:
        assert stem(first) == stem(second), f"{first}/{second} should share a stem"

    # Near-miss pairs an over-eager rule would wrongly fuse.
    unrelated = [
        ("bread", "breeze"), ("speed", "spend"), ("glass", "glaze"),
        ("ring", "rinse"), ("card", "care"), ("board", "boar"),
    ]
    for first, second in unrelated:
        assert stem(first) != stem(second), f"{first}/{second} must not collapse"

    vocabulary = {token for product in catalog.products for token in product.tokens}
    families: dict[str, set[str]] = {}
    for word in vocabulary:
        families.setdefault(stem(word), set()).add(word)

    for root, words in families.items():
        # A word that was already short is not the stemmer's doing; what must
        # never happen is *stemming* cutting one down to a promiscuous key.
        for word in words:
            if stem(word) != word:
                assert len(root) >= 3, f"{word!r} was cut down to {root!r}"
        # The largest real family in this catalogue is size/sized/sizes/sizing.
        assert len(words) <= 4, f"stem {root!r} swallowed {sorted(words)}"


def test_unknown_nonsense_still_returns_no_match(catalog):
    """Stemming must not turn a genuine miss into a false hit."""
    result = search.search_products(catalog, query="scuba diving regulator")
    assert result["status"] == "no_match"


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


def test_similar_will_not_offer_a_different_kind_of_thing(catalog):
    """Sharing a top-level category and a price band is not being an alternative.

    Beauty & Wellness holds the cork yoga mat (98 EUR), a safety razor (89) and a
    bath towel set (86). Ranking on price proximity once put the razor second and
    the towels third, and the agent duly invented a reason the towels suited yoga.
    """
    result = search.find_similar_products(catalog, product_id="BW-017", max_price_eur=90)
    ids = {p["product_id"] for p in result["products"]}

    assert "BW-007" not in ids          # Safety Razor Set, 89 EUR
    assert "BW-015" not in ids          # Turkish Bath Towel Set, 86 EUR
    assert ids == {"BW-018"}            # the one genuine fitness alternative in budget


def test_similar_ranks_kind_above_price_proximity(catalog):
    """The nearest-priced comparable must not automatically rank first."""
    target = catalog.get("BW-017")
    bands, razor = catalog.get("BW-018"), catalog.get("BW-007")

    assert search._similarity(bands, target) > search._similarity(razor, target)


def test_similar_says_when_there_is_only_one_honest_option(catalog):
    result = search.find_similar_products(catalog, product_id="BW-017")
    assert result["total_matches"] == 1
    assert any("only comparable product" in note for note in result["notes"])


def test_similar_reports_whether_more_were_held_back(catalog):
    result = search.find_similar_products(catalog, product_id="KD-001", limit=2)
    assert result["has_more"] is (result["total_matches"] > 2)


# --- Spanish queries -----------------------------------------------------------
# The shop answers in Spanish. Before the index carried both languages, a Spanish
# query only worked when the word happened to be spelled the same in English:
# `chef` and `yoga` matched, `cuchillo` and `vela` returned nothing.


@pytest.mark.parametrize(
    "query, expected_id",
    [
        ("una vela aromatica", "HL-003"),        # Ember Ceramic Candle
        ("un collar", "JW-001"),                 # Gold Vermeil Chain Necklace
        ("unos altavoces", "TG-025"),            # Bookshelf Speakers, Pair
        ("cuchillo de chef", "KD-001"),          # Chef's Knife 20cm
    ],
)
def test_spanish_queries_find_the_product(catalog_es, query, expected_id):
    result = search.search_products(catalog_es, query=query)
    assert result["status"] == "ok"
    assert result["products"][0]["product_id"] == expected_id


def test_spanish_stopword_no_longer_ranks_the_perfume(catalog_es):
    """`de` matched the name token in "Eau de Parfum" at the ranker's top weight."""
    for query in ("algo de cafe", "juegos de mesa", "cuchillo de chef"):
        ids = {p["product_id"] for p in search.search_products(catalog_es, query=query)["products"]}
        assert "BW-009" not in ids, f"perfume still leaking into {query!r}"


def test_spanish_shelf_names_find_the_shelf(catalog_es):
    """The taxonomy is never translated in the export, so it is indexed separately.

    `juegos de mesa` is what the Board Games subcategory is called in Spanish, and
    no product name contains the word `mesa` - it matched only literal tables.
    """
    result = search.search_products(catalog_es, query="juegos de mesa")
    assert result["products"][0]["product_id"] == "GP-001"

    ids = {p["product_id"] for p in result["products"]}
    assert not any(i.startswith("KD-") for i in ids), "cookware is not a board game"


def test_spanish_intent_reaches_the_right_shelf(catalog_es):
    """`dormir` is the Sleep subcategory. Nothing in those products says it."""
    result = search.search_products(catalog_es, query="algo para dormir mejor")
    assert {p["product_id"] for p in result["products"][:2]} == {"BW-001", "BW-002"}


def test_every_scoring_index_is_a_subset_of_the_matching_gate(catalog_es):
    """search_products gates on `tokens`, then scores `name_tokens`/`tag_tokens`.

    A term added to the finer indexes but not to the gate raises a product's score
    without letting it qualify - it scores 3.0 and is never considered.
    """
    for product in catalog_es.products:
        assert product.tag_tokens <= product.tokens, product.product_id
        assert product.name_tokens <= product.tokens, product.product_id


def test_spanish_query_for_an_unavailable_product_is_honest(catalog_es):
    result = search.search_products(catalog_es, query="esterilla de acupresion")
    assert result["reason"] == "out_of_stock_only"
    shown = result["suggestions"]["out_of_stock_matches"]
    assert shown[0]["product_id"] == "BW-012"


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
