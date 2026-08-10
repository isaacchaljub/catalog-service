"""Validating ingest pipeline for the gift shop catalogue export.

The CSV is one export from a system we do not control, produced by a client with no
data team. The next one will differ. So this module treats every field as hostile:

  * no row may crash startup - bad rows are quarantined with a reason and counted;
  * every scalar is coerced defensively, never `float(x)` on raw input;
  * every string field is capped at ingest, whether or not today's data needs it;
  * the filter vocabularies are derived from the data, not hardcoded, so a new
    `occasion` value in a future export becomes a legal filter instead of silently
    breaking search;
  * startup emits a data-quality report describing how dirty the input was.

Missing *required* columns raise at boot. That is deliberate: failing loudly at
startup is correct, failing quietly at request time is not.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.vocab_es import translate_color, translate_material

log = logging.getLogger(__name__)

# --- Caps. Applied always, not only when the current export needs them. ---------

MAX_NAME_CHARS = 120
MAX_BRAND_CHARS = 80
MAX_ATTRIBUTE_CHARS = 60          # color, material
MAX_DESCRIPTION_CHARS = 600
MAX_PITCH_CHARS = 140             # the one-line blurb the agent recommends from
MAX_LIST_ITEMS = 12               # tags, occasions
MAX_LIST_ITEM_CHARS = 40
MAX_PRICE_EUR = 1_000_000.0
IMPLAUSIBLE_SHIPPING_DAYS = 60

REQUIRED_COLUMNS = ("product_id", "name", "category", "price_eur")

TRUTHY = {"yes", "y", "true", "t", "1", "si", "sì"}
FALSY = {"no", "n", "false", "f", "0"}

# Small stopword list for the search token bag. Deliberately tiny - over-stemming
# hurts a 152-product catalogue more than it helps.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "in",
    "is", "it", "its", "of", "on", "or", "that", "the", "then", "this", "to",
    "was", "which", "with", "you", "your",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG = re.compile(r"<[^>]+>")
_ODD_SPACE = re.compile("[\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000\ufeff]")
_WHITESPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")
_TOKEN = re.compile(r"[a-z0-9]+")
_CURRENCY = re.compile(r"(?i)(eur|euros?|€|\$|£)")


# --- Records -------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """A soft problem with one row. The row survives; the issue is counted."""

    product_id: str
    field: str
    kind: str
    detail: str = ""


@dataclass
class DataQualityReport:
    source_file: str = ""
    rows_read: int = 0
    products_loaded: int = 0
    quarantined: dict[str, int] = field(default_factory=dict)
    soft_issues: dict[str, int] = field(default_factory=dict)
    duplicate_groups: int = 0
    encoding_fallback: str | None = None
    categories_normalized: dict[str, str] = field(default_factory=dict)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def quarantined_total(self) -> int:
        return sum(self.quarantined.values())

    def summary(self) -> dict[str, Any]:
        """The public-safe subset, exposed on /health. No per-row detail."""
        return {
            "rows_read": self.rows_read,
            "products_loaded": self.products_loaded,
            "quarantined_total": self.quarantined_total,
            "quarantined_by_reason": dict(self.quarantined),
            "duplicate_groups": self.duplicate_groups,
        }


@dataclass
class Product:
    product_id: str
    name: str
    price_eur: float

    category: str = "Uncategorised"
    category_slug: str = "uncategorised"
    category_key: str = ""
    category_raw: str = ""
    subcategory: str | None = None
    brand: str | None = None

    stock: int | None = None                 # None means *unknown*, distinct from 0
    stock_level: str = "unknown"             # unknown | out_of_stock | low | in_stock

    rating: float | None = None
    reviews_count: int | None = None

    recipient: str | None = None
    occasions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    color: str | None = None
    material: str | None = None
    gift_wrap: bool | None = None
    shipping_days: int | None = None

    description: str | None = None
    description_truncated: bool = False
    pitch: str | None = None
    pitch_truncated: bool = False

    # Spanish display text for the human-facing /p/ page only - never part of a tool
    # response, which always carries the English catalogue value (see app/search.py).
    name_es: str | None = None
    description_es: str | None = None
    color_es: str | None = None
    material_es: str | None = None

    duplicate_group_id: str | None = None
    also_in_categories: list[str] = field(default_factory=list)

    popularity: float = 0.0
    tokens: frozenset[str] = frozenset()
    name_tokens: frozenset[str] = frozenset()
    tag_tokens: frozenset[str] = frozenset()


@dataclass
class Catalog:
    products: list[Product]
    report: DataQualityReport
    by_id: dict[str, Product] = field(default_factory=dict)
    by_category_key: dict[str, list[Product]] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    category_slugs: dict[str, str] = field(default_factory=dict)   # slug -> display
    occasions: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)

    def get(self, product_id: str) -> Product | None:
        return self.by_id.get(normalize_product_id(product_id))


# --- Text hygiene --------------------------------------------------------------


def clean_text(value: Any) -> str:
    """Normalize one free-text field. Safe on None, bytes, numbers and markup."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    text = _CONTROL_CHARS.sub(" ", text)
    text = _ODD_SPACE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    # Stripping "<i>italics</i>." leaves "italics ." - tidy it, this is copy a
    # shopper will read.
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text).strip()


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cut to `limit` chars at a sentence boundary if possible, else a word boundary."""
    if len(text) <= limit:
        return text, False

    window = text[:limit]
    # Prefer a sentence end, provided it keeps at least a third of the budget -
    # a complete short sentence reads better in a chat bubble than a longer
    # fragment trailing an ellipsis. Below a third, a stray early full stop would
    # leave a uselessly short pitch, so fall through to the word boundary.
    best_end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if best_end >= limit // 3:
        return window[: best_end + 1].strip(), True

    space = window.rfind(" ")
    cut = window[:space] if space > 0 else window
    return cut.rstrip(" ,;:-") + "…", True


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "uncategorised"


def tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1}


def normalize_product_id(value: Any) -> str:
    return _WHITESPACE.sub("", clean_text(value)).upper()


# --- Scalar coercion -----------------------------------------------------------


def parse_money(value: Any) -> float | None:
    """Parse a price. Handles '42.00', '€1.234,56', '1,234.56', 'EUR 42', ' 42 '.

    Returns None when the value cannot be read as a non-negative amount - the caller
    treats that as a hard failure, because a product with no price cannot be
    recommended.
    """
    text = _CURRENCY.sub("", clean_text(value)).replace(" ", "")
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]

    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        # Whichever separator comes last is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        # ',' with 1-2 trailing digits is a decimal comma; otherwise thousands.
        text = (
            text.replace(",", ".")
            if len(text) - text.rfind(",") - 1 in (1, 2)
            else text.replace(",", "")
        )
    elif text.count(".") > 1:
        text = text.replace(".", "")            # 1.234.567 -> thousands separators

    try:
        amount = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount < 0 or amount > MAX_PRICE_EUR:
        return None
    return round(amount, 2)


def parse_int(value: Any) -> int | None:
    text = clean_text(value).replace(" ", "").replace(",", "").replace("_", "")
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    text = clean_text(value).replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_bool(value: Any) -> bool | None:
    text = clean_text(value).lower()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    return None


def split_multi(value: Any) -> list[str]:
    """Split a multi-value field. Prefers '|', falls back to ';', ',' then '/'."""
    text = clean_text(value)
    if not text:
        return []
    separator = next((s for s in ("|", ";", ",", "/") if s in text), None)
    parts = text.split(separator) if separator else [text]

    seen: list[str] = []
    for part in parts:
        item = clean_text(part).lower()[:MAX_LIST_ITEM_CHARS].strip()
        if item and item not in seen:
            seen.append(item)
    return seen[:MAX_LIST_ITEMS]


def category_key(value: str) -> str:
    """Fold the many spellings of one category onto a single key.

    'Home & Living', ' Home & Living', 'home & living' and 'Home and Living' all
    collapse to 'home & living'.
    """
    text = clean_text(value).casefold()
    text = re.sub(r"\s+and\s+", " & ", text)
    text = re.sub(r"\s*&\s*", " & ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _title_case_score(text: str) -> int:
    return sum(1 for word in text.split() if word[:1].isupper())


def choose_display_name(variants: Counter[str]) -> str:
    """Pick the best-looking raw spelling rather than imposing our own title case.

    Ranks by how title-cased a variant is, then by frequency, then alphabetically -
    deterministic, and it keeps the client's own capitalisation of things we would
    otherwise mangle.
    """
    return max(
        variants,
        key=lambda v: (_title_case_score(v), variants[v], [-ord(c) for c in v]),
    )


# --- Row parsing ---------------------------------------------------------------


def _normalize_header(name: str | None) -> str:
    text = clean_text(name).lower()
    return re.sub(r"[\s\-]+", "_", text).strip("_")


def parse_row(
    raw: dict[str, Any], seen_ids: set[str]
) -> tuple[Product | None, list[Issue], str | None]:
    """Coerce one CSV row.

    Returns (product, soft_issues, quarantine_reason). Exactly one of `product` and
    `quarantine_reason` is set.
    """
    issues: list[Issue] = []

    product_id = normalize_product_id(raw.get("product_id"))
    if not product_id:
        return None, issues, "missing_product_id"
    if product_id in seen_ids:
        return None, issues, "duplicate_product_id"

    name, name_cut = truncate(clean_text(raw.get("name")), MAX_NAME_CHARS)
    if not name:
        return None, issues, "missing_name"
    if name_cut:
        issues.append(Issue(product_id, "name", "truncated"))

    price = parse_money(raw.get("price_eur"))
    if price is None:
        return None, issues, "unparseable_price"

    product = Product(product_id=product_id, name=name, price_eur=price)

    # A short row leaves trailing columns as None (csv restval); a long one spills
    # into __extra__. Both mean the export's shape does not match its header.
    if raw.get("__extra__"):
        issues.append(Issue(product_id, "__row__", "ragged_row", "extra columns"))
    elif any(value is None for key, value in raw.items() if key != "__extra__"):
        issues.append(Issue(product_id, "__row__", "ragged_row", "missing columns"))

    # Category is resolved to a display name in a second pass; store the fold key
    # and the raw spelling so the pass can pick the best-looking variant.
    product.category_raw = clean_text(raw.get("category"))
    product.category_key = category_key(product.category_raw)
    if not product.category_key:
        issues.append(Issue(product_id, "category", "missing"))

    subcategory = clean_text(raw.get("subcategory"))
    product.subcategory = subcategory[:MAX_ATTRIBUTE_CHARS] or None

    brand = clean_text(raw.get("brand"))
    product.brand = brand[:MAX_BRAND_CHARS] or None

    # Stock: an unreadable value is *unknown*, which is not the same as zero.
    if clean_text(raw.get("stock")) == "":
        issues.append(Issue(product_id, "stock", "missing"))
    else:
        stock = parse_int(raw.get("stock"))
        if stock is None:
            issues.append(Issue(product_id, "stock", "unparseable", clean_text(raw.get("stock"))))
        else:
            product.stock = max(stock, 0)
            if stock < 0:
                issues.append(Issue(product_id, "stock", "negative_clamped"))
    product.stock_level = _stock_level(product.stock)

    rating = parse_float(raw.get("rating"))
    if rating is None:
        if clean_text(raw.get("rating")):
            issues.append(Issue(product_id, "rating", "unparseable"))
        else:
            issues.append(Issue(product_id, "rating", "missing"))
    elif 0.0 <= rating <= 5.0:
        product.rating = round(rating, 1)
    else:
        issues.append(Issue(product_id, "rating", "out_of_range", str(rating)))

    reviews = parse_int(raw.get("reviews_count"))
    if reviews is not None and reviews >= 0:
        product.reviews_count = reviews
    elif clean_text(raw.get("reviews_count")):
        issues.append(Issue(product_id, "reviews_count", "unparseable"))

    recipient = clean_text(raw.get("recipient")).lower()
    product.recipient = recipient[:MAX_ATTRIBUTE_CHARS] or None

    product.occasions = split_multi(raw.get("occasion"))
    if not product.occasions:
        issues.append(Issue(product_id, "occasion", "missing"))
    product.tags = split_multi(raw.get("tags"))

    color = clean_text(raw.get("color"))
    product.color = color[:MAX_ATTRIBUTE_CHARS] or None
    product.color_es = translate_color(product.color)
    if not product.color:
        issues.append(Issue(product_id, "color", "missing"))

    material = clean_text(raw.get("material"))
    product.material = material[:MAX_ATTRIBUTE_CHARS] or None
    product.material_es = translate_material(product.material)
    if not product.material:
        issues.append(Issue(product_id, "material", "missing"))

    product.gift_wrap = parse_bool(raw.get("gift_wrap"))
    if product.gift_wrap is None and clean_text(raw.get("gift_wrap")):
        issues.append(Issue(product_id, "gift_wrap", "unrecognised", clean_text(raw.get("gift_wrap"))))

    shipping = parse_int(raw.get("shipping_days"))
    if shipping is not None and shipping >= 0:
        product.shipping_days = shipping
        if shipping > IMPLAUSIBLE_SHIPPING_DAYS:
            issues.append(Issue(product_id, "shipping_days", "implausible", str(shipping)))
    elif clean_text(raw.get("shipping_days")):
        issues.append(Issue(product_id, "shipping_days", "unparseable"))

    description = clean_text(raw.get("description"))
    if description:
        product.description, product.description_truncated = truncate(
            description, MAX_DESCRIPTION_CHARS
        )
        product.pitch, product.pitch_truncated = truncate(description, MAX_PITCH_CHARS)
        if product.description_truncated:
            issues.append(Issue(product_id, "description", "truncated"))
    else:
        issues.append(Issue(product_id, "description", "missing"))

    return product, issues, None


def _stock_level(stock: int | None) -> str:
    if stock is None:
        return "unknown"
    if stock == 0:
        return "out_of_stock"
    return "low" if stock <= 5 else "in_stock"


# --- Loading -------------------------------------------------------------------


def _read_rows(path: Path, report: DataQualityReport) -> list[dict[str, Any]]:
    """Read the CSV with BOM handling, an encoding fallback and ragged-row tolerance."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle, restkey="__extra__", restval=None)
                if reader.fieldnames is None:
                    raise ValueError(f"{path} has no header row.")
                reader.fieldnames = [_normalize_header(n) for n in reader.fieldnames]
                rows = [dict(row) for row in reader]
            if encoding != "utf-8-sig":
                report.encoding_fallback = encoding
                log.warning("Catalogue decoded with fallback encoding %s", encoding)

            missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(
                    f"{path} is missing required column(s): {', '.join(missing)}. "
                    f"Found: {', '.join(reader.fieldnames or [])}"
                )
            known = set(REQUIRED_COLUMNS) | {
                "subcategory", "brand", "stock", "rating", "reviews_count", "recipient",
                "occasion", "tags", "color", "material", "gift_wrap", "shipping_days",
                "description", "__extra__",
            }
            report.unknown_columns = sorted(set(reader.fieldnames or []) - known)
            return rows
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode {path} as utf-8, cp1252 or latin-1.")


def _load_cached_pitches(path: Path) -> dict[str, dict[str, str]]:
    """Load precomputed pitches, keyed by product id, each carrying a source hash.

    Absent or unreadable is fine - ingest falls back to truncation.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable pitch cache %s: %s", path, exc)
        return {}


def description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]


def translation_hash(name: str, description: str) -> str:
    return hashlib.sha256(f"{name}\n{description}".encode("utf-8")).hexdigest()[:16]


def _load_translations(path: Path) -> dict[str, dict[str, str]]:
    """Load precomputed Spanish name/description, keyed by product id.

    Same shape and same staleness contract as the pitch cache: absent or unreadable
    is fine (the /p/ page falls back to the English catalogue value), and an entry
    whose hash no longer matches the current name+description is ignored rather than
    shown - a stale translation of a since-changed product is worse than none.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable translation cache %s: %s", path, exc)
        return {}


def _apply_translations(products: Iterable[Product], cache: dict[str, dict[str, str]]) -> int:
    applied = 0
    for product in products:
        entry = cache.get(product.product_id)
        if not entry:
            continue
        if entry.get("hash") != translation_hash(product.name, product.description or ""):
            continue                              # stale - fall back to English
        name_es = clean_text(entry.get("name_es"))[:MAX_NAME_CHARS]
        description_es = clean_text(entry.get("description_es"))[:MAX_DESCRIPTION_CHARS]
        if name_es:
            product.name_es = name_es
        if description_es:
            product.description_es = description_es
        if name_es or description_es:
            applied += 1
    return applied


def _apply_cached_pitches(products: Iterable[Product], cache: dict[str, dict[str, str]]) -> int:
    """Use a cached pitch only when it still matches the description it came from."""
    applied = 0
    for product in products:
        entry = cache.get(product.product_id)
        if not entry or not product.description:
            continue
        if entry.get("hash") != description_hash(product.description):
            continue                              # stale - fall back to truncation
        pitch = clean_text(entry.get("pitch"))[:MAX_PITCH_CHARS]
        if pitch:
            product.pitch, product.pitch_truncated = pitch, False
            applied += 1
    return applied


def _resolve_categories(products: list[Product], report: DataQualityReport) -> None:
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    for product in products:
        if product.category_key:
            variants[product.category_key][product.category_raw] += 1

    display_for = {key: choose_display_name(counts) for key, counts in variants.items()}
    for key, counts in variants.items():
        for raw in counts:
            if raw != display_for[key]:
                report.categories_normalized[raw] = display_for[key]

    for product in products:
        display = display_for.get(product.category_key, "Uncategorised")
        product.category = display
        product.category_slug = slugify(display)
        if not product.category_key:
            product.category_key = "uncategorised"


def _group_duplicates(products: list[Product]) -> int:
    """Detect the same product cross-listed under two ids and link the group."""
    groups: dict[tuple[str, str, float], list[Product]] = defaultdict(list)
    for product in products:
        key = (product.name.casefold(), (product.brand or "").casefold(), product.price_eur)
        groups[key].append(product)

    duplicate_groups = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        duplicate_groups += 1
        group_id = min(member.product_id for member in members)
        for member in members:
            member.duplicate_group_id = group_id
            member.also_in_categories = sorted(
                {other.category for other in members if other.category != member.category}
            )
    return duplicate_groups


def _index_product(product: Product) -> None:
    product.name_tokens = frozenset(tokenize(product.name))
    product.tag_tokens = frozenset(
        tokenize(" ".join(product.tags + ([product.subcategory] if product.subcategory else [])))
    )
    product.tokens = frozenset(
        tokenize(
            " ".join(
                filter(
                    None,
                    [
                        product.name, product.category, product.subcategory, product.brand,
                        product.description, " ".join(product.tags), " ".join(product.occasions),
                    ],
                )
            )
        )
    )
    if product.rating is not None:
        product.popularity = product.rating * math.log10((product.reviews_count or 0) + 10)


def load_catalog(
    csv_path: Path,
    pitches_path: Path | None = None,
    translations_path: Path | None = None,
) -> Catalog:
    """Read, validate and index the catalogue. Raises only on unusable input."""
    report = DataQualityReport(source_file=str(csv_path))
    rows = _read_rows(csv_path, report)
    report.rows_read = len(rows)

    products: list[Product] = []
    seen_ids: set[str] = set()
    quarantined: Counter[str] = Counter()
    soft: Counter[str] = Counter()

    for raw in rows:
        product, issues, reason = parse_row(raw, seen_ids)
        if reason:
            quarantined[reason] += 1
            log.warning("Quarantined row (%s): %s", reason, str(raw)[:200])
            continue
        assert product is not None
        seen_ids.add(product.product_id)
        products.append(product)
        for issue in issues:
            soft[f"{issue.field}:{issue.kind}"] += 1

    report.quarantined = dict(quarantined)
    report.soft_issues = dict(soft)
    report.products_loaded = len(products)

    _resolve_categories(products, report)
    report.duplicate_groups = _group_duplicates(products)

    if pitches_path is not None:
        applied = _apply_cached_pitches(products, _load_cached_pitches(pitches_path))
        if applied:
            log.info("Applied %d precomputed pitches", applied)

    if translations_path is not None:
        applied_es = _apply_translations(products, _load_translations(translations_path))
        if applied_es:
            log.info("Applied %d Spanish translations", applied_es)

    for product in products:
        _index_product(product)

    catalog = _build_indexes(products, report)
    log.info(
        "Catalogue loaded: %d/%d rows, %d quarantined, %d categories, %d duplicate groups",
        report.products_loaded, report.rows_read, report.quarantined_total,
        len(catalog.categories), report.duplicate_groups,
    )
    return catalog


def _build_indexes(products: list[Product], report: DataQualityReport) -> Catalog:
    by_id = {product.product_id: product for product in products}
    by_category_key: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        by_category_key[product.category_key].append(product)

    # Vocabularies are derived from the data, never hardcoded.
    category_counts = Counter(product.category for product in products)
    occasion_counts = Counter(o for product in products for o in product.occasions)
    recipient_counts = Counter(p.recipient for p in products if p.recipient)

    def ranked(counts: Counter[str]) -> list[str]:
        return [value for value, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    return Catalog(
        products=products,
        report=report,
        by_id=by_id,
        by_category_key=dict(by_category_key),
        categories=ranked(category_counts),
        category_slugs={slugify(name): name for name in category_counts},
        occasions=ranked(occasion_counts),
        recipients=ranked(recipient_counts),
    )
