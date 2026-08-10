"""Deterministic placeholder art for products the export has no photo for.

A single image repeated across every product broke the illusion once we confirmed
indigo.ai renders markdown images inline: two different products showing the
identical picture in the same reply reads as fake, not MVP. This generates a small,
self-hosted SVG instead - a colour per category, a monogram from the product name -
so two different recommendations at least look different from each other. It is
never claimed to be a photo.

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
_CREAM = "#FBF4EC"


def render(name: str, category: str | None) -> str:
    color = _CATEGORY_COLORS.get(category or "", _DEFAULT_COLOR)
    initial = escape((name.strip()[:1] or "?").upper())
    label = escape(category or "Focolare")

    # Markdown image syntax carries no size attribute, so the SVG's own aspect ratio
    # is the only lever over display size in a chat reply - a 1:1 square scales to
    # the full chat-column width and dominates the screen. A short, wide banner scales
    # to a much shorter height at the same width.
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200" \
viewBox="0 0 600 200" role="img" aria-label="{escape(name)}">
<rect width="600" height="200" rx="16" fill="{color}"/>
<text x="50" y="138" font-family="Georgia, 'Iowan Old Style', serif" font-size="100"
      fill="{_CREAM}" opacity="0.92">{initial}</text>
<text x="550" y="175" font-family="-apple-system, Helvetica, sans-serif" font-size="20"
      fill="{_CREAM}" text-anchor="end" opacity="0.75">{label}</text>
</svg>"""
