"""Deterministic placeholder art for products the export has no photo for.

A single image repeated across every product broke the illusion once we confirmed
indigo.ai renders markdown images inline: two different products showing the
identical picture in the same reply reads as fake, not MVP. This generates a small,
self-hosted SVG instead - a colour and a simple hand-drawn icon per category - so two
different recommendations at least look different from each other. It is never
claimed to be a photo: real web images were considered and rejected (licensing risk
with paid stock, and even free-licensed stock would show someone else's product, not
this one - see README for the fuller argument). These icons are simple geometric
primitives (circles, rects, straight-line paths) rather than intricate illustrations,
checked by rendering a contact sheet and eyeballing it - a couple of first attempts
(Kitchen & Dining, Kids) read as the wrong thing entirely on the first pass.

The catalogue is one export, assumed hostile (see app/ingest.py): product names and
categories are escaped before they reach the markup, both for safety and because an
unescaped '&' in a name would make the SVG invalid XML, not just unsafe.
"""

from __future__ import annotations

from html import escape

# One colour per canonical category, chosen from the landing page's warm palette
# family rather than hashed, so nothing clashes or repeats by accident. Keys must
# match app.ingest's canonical category names exactly.
_CATEGORY_COLORS = {
    "Home & Living": "#BC4B2C",
    "Tech & Gadgets": "#4A6670",
    "Kitchen & Dining": "#9C6B30",
    "Beauty & Wellness": "#B06A82",
    "Books & Stationery": "#5B7553",
    "Games & Puzzles": "#C79A4B",
    "Outdoor & Travel": "#6E8B5E",
    "Jewellery": "#8B6F9E",
    "Kids": "#4F92A6",
    "Pets": "#B5652F",
    "Experiences": "#A2452F",
}
_DEFAULT_COLOR = "#8A7B6C"

# Spanish label for the category name printed on the image itself. Separate from
# app.vocab_es because this is a UI label for a fixed set of 11 categories, not a
# glossary derived from free-text catalogue values.
_CATEGORY_ES = {
    "Home & Living": "Hogar",
    "Tech & Gadgets": "Tecnología",
    "Kitchen & Dining": "Cocina",
    "Beauty & Wellness": "Belleza y Bienestar",
    "Books & Stationery": "Libros y Papelería",
    "Games & Puzzles": "Juegos y Puzles",
    "Outdoor & Travel": "Aire Libre y Viajes",
    "Jewellery": "Joyería",
    "Kids": "Niños",
    "Pets": "Mascotas",
    "Experiences": "Experiencias",
}
_CREAM = "#FBF4EC"

# Each icon is drawn on a 24x24 grid. `{c}` is the icon colour (cream, matching the
# label text); a few icons use `{bg}` - the category's own swatch colour - painted on
# top to fake a cut-out notch against a flat background (the puzzle piece's bite, the
# pinwheel's centre hole). That trick only works because the background is a single
# flat fill, which it always is here.
_ICONS = {
    "Home & Living": """
        <path d="M4 11 L12 4 L20 11" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M6 11 V20 H18 V11" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        <rect x="10" y="15" width="4" height="5" fill="none" stroke="{c}" stroke-width="1.4"/>
    """,
    "Tech & Gadgets": """
        <rect x="7" y="3" width="10" height="18" rx="2" fill="none" stroke="{c}" stroke-width="1.6"/>
        <circle cx="12" cy="18" r="0.9" fill="{c}" stroke="none"/>
    """,
    "Kitchen & Dining": """
        <rect x="6" y="11" width="12" height="8" rx="1" fill="none" stroke="{c}" stroke-width="1.6"/>
        <line x1="4.5" y1="11" x2="19.5" y2="11" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/>
        <rect x="3" y="10.3" width="2.6" height="1.6" rx="0.8" fill="{c}" stroke="none"/>
        <rect x="18.4" y="10.3" width="2.6" height="1.6" rx="0.8" fill="{c}" stroke="none"/>
        <line x1="12" y1="11" x2="12" y2="8" stroke="{c}" stroke-width="1.4" stroke-linecap="round"/>
        <circle cx="12" cy="7" r="1.1" fill="{c}" stroke="none"/>
    """,
    "Beauty & Wellness": """
        <circle cx="12" cy="8" r="3" fill="none" stroke="{c}" stroke-width="1.4"/>
        <circle cx="12" cy="16" r="3" fill="none" stroke="{c}" stroke-width="1.4"/>
        <circle cx="8" cy="12" r="3" fill="none" stroke="{c}" stroke-width="1.4"/>
        <circle cx="16" cy="12" r="3" fill="none" stroke="{c}" stroke-width="1.4"/>
        <circle cx="12" cy="12" r="1.5" fill="{c}" stroke="none"/>
    """,
    "Books & Stationery": """
        <rect x="5" y="4" width="14" height="16" rx="1" fill="none" stroke="{c}" stroke-width="1.6"/>
        <line x1="12" y1="4" x2="12" y2="20" stroke="{c}" stroke-width="1.6"/>
        <line x1="8" y1="8" x2="10" y2="8" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>
        <line x1="8" y1="11" x2="10" y2="11" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>
        <line x1="14" y1="8" x2="16" y2="8" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>
        <line x1="14" y1="11" x2="16" y2="11" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>
    """,
    "Games & Puzzles": """
        <rect x="5" y="5" width="14" height="14" rx="2" fill="{c}"/>
        <circle cx="19" cy="12" r="2.6" fill="{c}"/>
        <circle cx="9" cy="5" r="2.6" fill="{bg}"/>
    """,
    "Outdoor & Travel": """
        <circle cx="17" cy="7" r="2" fill="none" stroke="{c}" stroke-width="1.4"/>
        <path d="M3 19 L9 9 L13 15 L16 11 L21 19 Z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
    """,
    "Jewellery": """
        <path d="M6 9 L12 3 L18 9 L12 21 Z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>
        <line x1="6" y1="9" x2="18" y2="9" stroke="{c}" stroke-width="1.2"/>
        <line x1="9" y1="9" x2="12" y2="21" stroke="{c}" stroke-width="1.2"/>
        <line x1="15" y1="9" x2="12" y2="21" stroke="{c}" stroke-width="1.2"/>
    """,
    "Kids": """
        <path d="M12 12 L12 4 L18 8 Z" fill="{c}"/>
        <path d="M12 12 L20 12 L16 18 Z" fill="{c}"/>
        <path d="M12 12 L12 20 L6 16 Z" fill="{c}"/>
        <path d="M12 12 L4 12 L8 6 Z" fill="{c}"/>
        <circle cx="12" cy="12" r="1.4" fill="{bg}"/>
    """,
    "Pets": """
        <ellipse cx="12" cy="16" rx="4.2" ry="3.4" fill="{c}"/>
        <circle cx="6.5" cy="10" r="1.9" fill="{c}"/>
        <circle cx="11.3" cy="6.8" r="1.9" fill="{c}"/>
        <circle cx="16.3" cy="7.2" r="1.9" fill="{c}"/>
        <circle cx="19.5" cy="11" r="1.9" fill="{c}"/>
    """,
    "Experiences": """
        <rect x="4" y="9" width="16" height="11" rx="1" fill="none" stroke="{c}" stroke-width="1.6"/>
        <line x1="4" y1="13.5" x2="20" y2="13.5" stroke="{c}" stroke-width="1.4"/>
        <line x1="12" y1="9" x2="12" y2="20" stroke="{c}" stroke-width="1.4"/>
        <path d="M12 9 C10 5 6 5.5 6.5 8 C7 9.5 10 9.3 12 9 Z" fill="{c}"/>
        <path d="M12 9 C14 5 18 5.5 17.5 8 C17 9.5 14 9.3 12 9 Z" fill="{c}"/>
    """,
}
# A generic gift-tag icon for any category name that doesn't match the table above
# (defensive - the export's category vocabulary is closed, but nothing should crash
# rendering if it ever changes).
_DEFAULT_ICON = """
    <path d="M4 11 L11 4 L20 4 L20 13 L13 20 Z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>
    <circle cx="16" cy="8" r="1.4" fill="{c}" stroke="none"/>
"""


def render(name: str, category: str | None) -> str:
    color = _CATEGORY_COLORS.get(category or "", _DEFAULT_COLOR)
    icon = _ICONS.get(category or "", _DEFAULT_ICON).format(c=_CREAM, bg=color)
    label = escape(_CATEGORY_ES.get(category or "", category or "Focolare"))

    # Markdown image syntax carries no size attribute, so the SVG's own aspect ratio
    # is the only lever over display size in a chat reply - a 1:1 square scales to
    # the full chat-column width and dominates the screen. A short, wide banner scales
    # to a much shorter height at the same width.
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200" \
viewBox="0 0 600 200" role="img" aria-label="{escape(name)}">
<rect width="600" height="200" rx="16" fill="{color}"/>
<g transform="translate(44,46) scale(4.5)">{icon}</g>
<text x="550" y="175" font-family="-apple-system, Helvetica, sans-serif" font-size="20"
      fill="{_CREAM}" text-anchor="end" opacity="0.75">{label}</text>
</svg>"""
