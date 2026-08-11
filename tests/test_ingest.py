"""Ingest is tested against a fixture of deliberately hostile input.

None of these malformations exist in the export we were given. That is the point:
the guards must hold for the next export, not just this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest import (
    MAX_DESCRIPTION_CHARS,
    MAX_PITCH_CHARS,
    STOPWORDS,
    clean_text,
    load_catalog,
    parse_bool,
    parse_money,
    split_multi,
    tokenize,
    translation_hash,
    truncate,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nasty-catalog.csv"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(FIXTURE, None)


# --- The pipeline survives at all ----------------------------------------------


def test_loads_without_raising(catalog):
    assert catalog.report.products_loaded > 0


def test_bad_rows_are_quarantined_not_fatal(catalog):
    assert catalog.report.quarantined == {
        "missing_product_id": 1,
        "missing_name": 1,
        "unparseable_price": 1,
        "duplicate_product_id": 1,
    }
    assert catalog.report.products_loaded == catalog.report.rows_read - 4


def test_duplicate_id_keeps_the_first_row(catalog):
    assert catalog.get("NA-001").name == "Control Product"


def test_bom_and_crlf_do_not_corrupt_the_first_column(catalog):
    # A BOM left in place would make the first header "﻿product_id" and every
    # id lookup would miss.
    assert catalog.get("NA-001") is not None
    assert catalog.report.encoding_fallback is None


def test_missing_required_column_fails_loudly(tmp_path):
    broken = tmp_path / "broken.csv"
    broken.write_text("product_id,name,category\nX-1,Thing,Home\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required column"):
        load_catalog(broken, None)


# --- Numeric coercion ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42.00", 42.00),
        ("  42  ", 42.00),
        ("€1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("EUR 89", 89.00),
        ("6,5", 6.50),
        ("banana", None),
        ("", None),
        ("-5.00", None),
        ("99999999999", None),
        ("1.234.567", None),      # thousands-grouped, but beyond the sanity cap
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def test_absurd_prices_are_rejected_rather_than_recommended():
    assert parse_money("5000000") is None


def test_european_and_anglo_prices_agree(catalog):
    assert catalog.get("NA-002").price_eur == 1234.56
    assert catalog.get("NA-003").price_eur == 1234.56


def test_unknown_stock_is_not_out_of_stock(catalog):
    """The distinction that matters: we do not know, versus we know there are none."""
    product = catalog.get("NA-004")
    assert product.stock is None
    assert product.stock_level == "unknown"
    assert product.stock_level != "out_of_stock"


def test_negative_stock_clamps_to_zero(catalog):
    assert catalog.get("NA-020").stock == 0
    assert catalog.get("NA-020").stock_level == "out_of_stock"


def test_rating_outside_the_scale_is_dropped_entirely(catalog):
    # Never null, never 0 - the key is absent, so a model cannot report "rated 0".
    assert catalog.get("NA-005").rating is None


def test_decimal_comma_rating(catalog):
    assert catalog.get("NA-022").rating == 4.6


def test_implausible_shipping_is_kept_but_flagged(catalog):
    assert catalog.get("NA-021").shipping_days == 999
    assert catalog.report.soft_issues.get("shipping_days:implausible") == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("yes", True), ("Y", True), ("TRUE", True), ("no", False), ("0", False),
     ("maybe", None), ("", None)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


def test_unrecognised_gift_wrap_is_omitted(catalog):
    assert catalog.get("NA-016").gift_wrap is None


# --- Text hygiene --------------------------------------------------------------


def test_html_is_stripped_and_entities_unescaped(catalog):
    description = catalog.get("NA-010").description
    assert "<b>" not in description and "&amp;" not in description
    assert description == "Bold claim with an & entity and italics."


def test_control_characters_are_removed(catalog):
    description = catalog.get("NA-015").description
    assert not any(ord(char) < 32 for char in description)
    assert description == "Bell and vertical tab inside."


def test_embedded_newline_survives_as_one_field(catalog):
    assert catalog.get("NA-019").description == "First line. Second line."


def test_non_breaking_space_becomes_a_normal_space(catalog):
    name = catalog.get("NA-012").name
    assert " " not in name
    assert name.startswith("Café Press")


def test_clean_text_is_safe_on_none_and_numbers():
    assert clean_text(None) == ""
    assert clean_text(42) == "42"


# --- Length caps ---------------------------------------------------------------


def test_long_description_is_capped_and_flagged(catalog):
    product = catalog.get("NA-011")
    assert len(product.description) <= MAX_DESCRIPTION_CHARS
    assert product.description_truncated is True


def test_pitch_is_capped_regardless_of_input(catalog):
    """The context-window guard. Holds whether or not today's data needs it."""
    product = catalog.get("NA-011")
    assert len(product.pitch) <= MAX_PITCH_CHARS
    assert product.pitch_truncated is True


def test_every_pitch_respects_the_cap(catalog):
    assert all(len(p.pitch) <= MAX_PITCH_CHARS for p in catalog.products if p.pitch)


def test_truncate_prefers_a_sentence_boundary():
    text = "First sentence here. Second sentence runs on much longer than the limit."
    cut, was_cut = truncate(text, 40)
    assert was_cut is True
    assert cut == "First sentence here."


def test_truncate_falls_back_to_a_word_boundary():
    cut, was_cut = truncate("a" * 10 + " " + "b" * 40, 20)
    assert was_cut is True
    assert cut.endswith("…")
    assert len(cut) <= 21


def test_truncate_leaves_short_text_alone():
    assert truncate("Short.", 100) == ("Short.", False)


# --- Multi-value fields --------------------------------------------------------


def test_comma_separated_tags_are_split(catalog):
    assert catalog.get("NA-013").tags == ["alpha", "beta", "gamma"]
    assert catalog.get("NA-013").occasions == ["birthday", "christmas"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a|b|c", ["a", "b", "c"]),
        ("a; b ;c", ["a", "b", "c"]),
        ("a/b", ["a", "b"]),
        ("Single", ["single"]),
        ("a|a|b", ["a", "b"]),
        ("", []),
        ("|||", []),
    ],
)
def test_split_multi(raw, expected):
    assert split_multi(raw) == expected


# --- Ragged rows ---------------------------------------------------------------


def test_ragged_rows_are_kept_and_counted(catalog):
    """A short or long row must not silently shift every field left."""
    assert catalog.report.soft_issues.get("__row__:ragged_row") == 2
    short_row = catalog.get("NA-017")
    assert short_row is not None
    assert short_row.price_eur == 30.00        # fields did not shift
    long_row = catalog.get("NA-018")
    assert long_row is not None
    assert long_row.description == "A description."


# --- Category folding ----------------------------------------------------------


def test_unknown_categories_are_accepted_not_dropped(catalog):
    assert "Sorcery" in catalog.categories


def test_case_variants_fold_together(catalog):
    assert catalog.get("NA-014").category == catalog.get("NA-024").category == "Sorcery"


def test_vocabularies_are_derived_from_the_data(catalog):
    """A value we never hardcoded still becomes a legal filter."""
    assert "birthday" in catalog.occasions
    assert catalog.categories == sorted(set(catalog.categories), key=catalog.categories.index)


# --- Spanish translation cache (display-only, never a tool value) --------------


def test_translation_applied_when_hash_matches(tmp_path):
    cache = {
        "NA-001": {
            "hash": translation_hash("Control Product", "A perfectly ordinary product."),
            "name_es": "Producto de Control",
            "description_es": "Un producto perfectamente normal.",
        }
    }
    path = tmp_path / "translations_es.json"
    path.write_text(json.dumps(cache), encoding="utf-8")

    translated = load_catalog(FIXTURE, None, path)
    product = translated.get("NA-001")
    assert product.name_es == "Producto de Control"
    assert product.description_es == "Un producto perfectamente normal."


def test_stale_translation_is_ignored(tmp_path):
    """The name/description changed since the cache was built - fall back to English."""
    cache = {
        "NA-001": {
            "hash": translation_hash("Some Old Name", "Some old description."),
            "name_es": "Nombre Antiguo",
            "description_es": "Descripción antigua.",
        }
    }
    path = tmp_path / "translations_es.json"
    path.write_text(json.dumps(cache), encoding="utf-8")

    translated = load_catalog(FIXTURE, None, path)
    product = translated.get("NA-001")
    assert product.name_es is None
    assert product.description_es is None


def test_missing_translations_file_is_fine(tmp_path):
    translated = load_catalog(FIXTURE, None, tmp_path / "does-not-exist.json")
    assert translated.get("NA-001").name_es is None


def test_spanish_function_words_are_stripped():
    """`de` is the one that mattered: it collided with the name "Eau de Parfum"."""
    assert tokenize("cuchillo de chef") == {"cuchillo", "chef"}
    assert tokenize("juegos de mesa") == {"juegos", "mesa"}
    assert tokenize("algo para la cocina") == {"cocina"}


def test_accents_fold_rather_than_splitting_the_word():
    """Without folding, `_TOKEN` cut the word at the accent and dropped the rest."""
    assert tokenize("acupresión") == tokenize("acupresion") == {"acupresion"}
    assert tokenize("café") == {"cafe"}


def test_tea_survives_the_spanish_stopwords():
    """`té` folds to `te`, which is also a pronoun. The catalogue sells tea."""
    assert "te" not in STOPWORDS
    assert tokenize("té") == {"te"}
