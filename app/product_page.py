"""Human-facing HTML for a single product.

Not a tool and not in the OpenAPI spec - linked from `product_url` in tool responses
and clicked by a shopper, not called by the model. Reuses the landing page's palette
so someone who clicks through doesn't land somewhere that looks like a different site.

The catalogue is one export, assumed hostile (see app/ingest.py) - every value below
is escaped before it reaches the page, the same as any other untrusted input rendered
into HTML.
"""

from __future__ import annotations

from html import escape
from typing import Any

LANDING_PAGE_URL = "https://isaacchaljub.github.io/catalog-service/"

_STYLE = """
:root{
  --bg:#FBF4EC; --surface:#FFFFFF; --card-border:#EADFCE;
  --text:#2B211B; --text-muted:#6B5D51; --primary:#BC4B2C; --primary-dark:#9C3C21;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
     font-family:-apple-system,"Segoe UI",system-ui,sans-serif;line-height:1.5}
h1{font-family:Georgia,"Iowan Old Style",ui-serif,serif;font-weight:600}
a{color:var(--primary-dark)}
.wrap{max-width:640px;margin:0 auto;padding:0 24px}
header{padding:20px 0;border-bottom:1px solid var(--card-border)}
header a{text-decoration:none;font-family:Georgia,ui-serif,serif;font-size:1.1rem;color:var(--text)}
main{padding:40px 0 64px}
.price{font-size:1.4rem;font-weight:700;color:var(--primary-dark);margin:6px 0 18px}
.note{background:var(--surface);border:1px solid var(--card-border);border-radius:10px;
      padding:12px 16px;margin:0 0 20px;font-size:0.92rem}
.note.stock{border-color:#E0B9A6;background:#FBEDE6}
dl{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;margin:24px 0;font-size:0.92rem}
dt{color:var(--text-muted)}
dd{margin:0}
.hero-img{width:160px;height:160px;border-radius:14px;display:block;margin:0 0 20px}
.alts{margin-top:32px}
.alts a{display:block;padding:10px 0;border-top:1px solid var(--card-border)}
footer{border-top:1px solid var(--card-border);padding:24px 0;text-align:center;
       color:var(--text-muted);font-size:0.82rem}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<header><div class="wrap"><a href="{escape(LANDING_PAGE_URL)}">&larr; Focolare</a></div></header>
<main><div class="wrap">{body}</div></main>
<footer>Focolare &mdash; cat&aacute;logo de demostraci&oacute;n para la evaluaci&oacute;n t&eacute;cnica de indigo.ai.</footer>
</body>
</html>"""


def render_not_found(product_id: str) -> str:
    body = f"""
<h1>No encontramos ese producto</h1>
<p>No existe ning&uacute;n producto con el identificador &laquo;{escape(product_id)}&raquo;.</p>
<p><a href="{escape(LANDING_PAGE_URL)}">Volver a la tienda</a></p>
"""
    return _page("Producto no encontrado - Focolare", body)


def _detail_rows(product: dict[str, Any]) -> str:
    rows: list[tuple[str, str]] = []
    if product.get("brand"):
        rows.append(("Marca", product["brand"]))
    if product.get("color"):
        rows.append(("Color", product["color"]))
    if product.get("material"):
        rows.append(("Material", product["material"]))
    if product.get("shipping_days") is not None:
        days = product["shipping_days"]
        rows.append(("Envío", "Inmediato (digital)" if days == 0 else f"{days} días laborables"))
    if product.get("gift_wrap"):
        rows.append(("Envoltorio", "Se envuelve para regalo"))
    if not rows:
        return ""
    items = "".join(f"<dt>{escape(k)}</dt><dd>{escape(str(v))}</dd>" for k, v in rows)
    return f"<dl>{items}</dl>"


def render_product(product: dict[str, Any], alternatives: list[dict[str, Any]] | None) -> str:
    name = escape(product["name"])
    price = f"{product['price_eur']:g} €"
    stock = product.get("stock_level")
    image = f'<img class="hero-img" src="/p/{escape(product["product_id"])}/image.svg" alt="">'

    stock_note = ""
    if stock == "out_of_stock":
        stock_note = '<div class="note stock">Agotado en este momento.</div>'
    elif stock == "low":
        stock_note = '<div class="note stock">Quedan pocas unidades.</div>'
    elif stock == "unknown":
        stock_note = '<div class="note">No podemos confirmar el estado del stock ahora mismo.</div>'

    rating_line = ""
    if product.get("rating") is not None:
        count = product.get("reviews_count")
        suffix = f" ({count} opiniones)" if count else ""
        rating_line = f'<p>&#9733; {product["rating"]:g}{escape(suffix)}</p>'

    description = escape(product.get("description") or product.get("pitch") or "")

    alt_html = ""
    if alternatives:
        links = "".join(
            f'<a href="{escape(a["product_url"])}">{escape(a["name"])} '
            f'&mdash; {a["price_eur"]:g} €</a>'
            for a in alternatives
            if a.get("product_url")
        )
        if links:
            alt_html = f'<div class="alts"><h2>Alternativas disponibles</h2>{links}</div>'

    body = f"""
{image}
<h1>{name}</h1>
<p class="price">{price}</p>
{stock_note}
{rating_line}
<p>{description}</p>
{_detail_rows(product)}
{alt_html}
"""
    return _page(f"{product['name']} - Focolare", body)
