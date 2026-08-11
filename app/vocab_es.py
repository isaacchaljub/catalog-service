"""Spanish translations for the catalogue's closed colour/material vocabulary.

Unlike name/description, colour and material don't need the bulk-translation cache
in data/translations_es.json: reading every distinct value the current export uses
turns up 49 colours and ~90 material words (materials are comma-separated compounds,
e.g. "Steel, oak", built from a small shared vocabulary) - small and stable enough to
be a fixed glossary checked into code, the same way filter_vocabulary is derived from
the data rather than invented. Anything outside this glossary - a colour the next
export introduces - falls back to the original English word rather than guessing.
"""

from __future__ import annotations

# Keys are casefolded English. Built by reading every distinct value in the current
# export (see the catalogue), not guessed in advance.
COLOR_ES: dict[str, str] = {
    "amber": "Ámbar",
    "beige": "Beige",
    "black": "Negro",
    "blue": "Azul",
    "blush": "Rosa Empolvado",
    "brass": "Latón",
    "brown": "Marrón",
    "burgundy": "Burdeos",
    "chalk": "Blanco Tiza",
    "charcoal": "Carbón",
    "check": "A Cuadros",
    "chrome": "Cromado",
    "clay": "Arcilla",
    "clear": "Transparente",
    "copper": "Cobre",
    "cream": "Crema",
    "forest": "Verde Bosque",
    "gold": "Dorado",
    "graphite": "Grafito",
    "green": "Verde",
    "grey": "Gris",
    "gunmetal": "Gris Metálico",
    "indigo": "Índigo",
    "ivory": "Marfil",
    "midnight": "Azul Medianoche",
    "mint": "Menta",
    "mixed": "Combinado",
    "multi": "Multicolor",
    "natural": "Natural",
    "navy": "Azul Marino",
    "oak": "Roble",
    "oatmeal": "Beige Avena",
    "off white": "Blanco Roto",
    "olive": "Verde Oliva",
    "pearl": "Perla",
    "red": "Rojo",
    "sage": "Verde Salvia",
    "sand": "Arena",
    "silver": "Plateado",
    "slate": "Gris Pizarra",
    "speckled grey": "Gris Moteado",
    "steel": "Gris Acero",
    "tan": "Beige Tostado",
    "teal": "Verde Azulado",
    "terracotta": "Terracota",
    "walnut": "Nogal",
    "warm white": "Blanco Cálido",
    "white": "Blanco",
    "yellow": "Amarillo",
}

# Keys are casefolded English tokens as they appear after splitting a material value
# on ",". Materials themselves are compounds ("Steel, oak") reassembled from these.
MATERIAL_WORD_ES: dict[str, str] = {
    "100gsm paper": "papel de 100 g/m²",
    "22 momme silk": "seda de 22 momme",
    "90gsm paper": "papel de 90 g/m²",
    "abs": "ABS",
    "aluminium": "aluminio",
    "aluminium tube": "tubo de aluminio",
    "baby alpaca": "alpaca bebé",
    "beech": "haya",
    "birch ply": "contrachapado de abedul",
    "board": "cartón",
    "borosilicate glass": "vidrio de borosilicato",
    "brass": "latón",
    "canvas": "lona",
    "carbon steel": "acero al carbono",
    "card": "cartulina",
    "ceramic": "cerámica",
    "cloth": "tela",
    "copper": "cobre",
    "cord": "cordón",
    "cork": "corcho",
    "cotton": "algodón",
    "cotton paper": "papel de algodón",
    "cotton rope": "cuerda de algodón",
    "enamelled cast iron": "hierro fundido esmaltado",
    "enamelled steel": "acero esmaltado",
    "eva foam": "espuma EVA",
    "fabric": "tela",
    "felt": "fieltro",
    "film": "película",
    "foam": "espuma",
    "freshwater pearl": "perla de agua dulce",
    "full-grain leather": "cuero de flor entera",
    "glass": "vidrio",
    "glass beads": "cuentas de vidrio",
    "glass jar": "tarro de vidrio",
    "gold vermeil": "vermeil de oro",
    "gold vermeil on silver": "vermeil de oro sobre plata",
    "ink": "tinta",
    "jute": "yute",
    "latex": "látex",
    "lead-free crystal": "cristal sin plomo",
    "leather": "cuero",
    "linen": "lino",
    "linen shade": "pantalla de lino",
    "linen thread": "hilo de lino",
    "maple": "arce",
    "marble": "mármol",
    "mdf": "MDF",
    "memory foam": "espuma viscoelástica",
    "oak": "roble",
    "olive wood": "madera de olivo",
    "organic cotton": "algodón orgánico",
    "pakkawood": "pakkawood",
    "paper": "papel",
    "pbt": "PBT",
    "plastic": "plástico",
    "plush": "peluche",
    "plywood": "contrachapado",
    "protein leather": "cuero de proteína",
    "pu backing": "refuerzo de PU",
    "pu leather": "cuero sintético (PU)",
    "rattan": "ratán",
    "recycled fabric": "tela reciclada",
    "recycled nylon": "nailon reciclado",
    "recycled plastic": "plástico reciclado",
    "resin": "resina",
    "ripstop nylon": "nailon ripstop",
    "rubber": "caucho",
    "seagrass": "fibra de alga marina",
    "seed": "semilla",
    "sheepskin": "piel de oveja",
    "silicone": "silicona",
    "silk": "seda",
    "silver": "plata",
    "sisal": "sisal",
    "slate": "pizarra",
    "soap": "jabón",
    "solid oak": "roble macizo",
    "soy wax": "cera de soja",
    "stainless steel": "acero inoxidable",
    "steel": "acero",
    "steel mesh": "malla de acero",
    "steel nib": "plumín de acero",
    "sterling silver": "plata de ley",
    "stoneware": "gres",
    "tea": "té",
    "terracotta": "terracota",
    "terrazzo": "terrazo",
    "tin": "estaño",
    "titanium": "titanio",
    "turkish cotton": "algodón turco",
    "vg-10 steel": "acero VG-10",
    "walnut": "nogal",
    "walnut box": "caja de nogal",
    "walnut veneer": "chapa de nogal",
    "washed linen": "lino lavado",
    "water-based paint": "pintura al agua",
    "wax": "cera",
    "waxed canvas": "lona encerada",
    "wood": "madera",
    "wool": "lana",
    "wool blend": "mezcla de lana",
    "zirconia": "circonia",
}


# The taxonomy is never translated in the export, and it is the one part of a product
# that says what *kind* of thing it is. Indexing only the English label meant a shopper
# searching the Spanish name of a whole shelf found nothing: "juegos de mesa" is what
# every Spanish speaker calls the Board Games subcategory, and it matched only literal
# tables, because no product name contains the word "mesa".
#
# These are search terms, not display strings - several entries carry more than one
# word because shoppers do not all reach for the same one ("sueño" and "dormir" are
# the same shelf). Values are indexed, never shown. Accents are folded at tokenise
# time, so they are written naturally here.
#
# Deliberately NOT giving Kitchen & Dining the word "mesa": it would make "juegos de
# mesa" match cookware again, which is the exact bug this fixes.
SEARCH_TERMS_ES: dict[str, str] = {
    # Categories
    "beauty & wellness": "belleza bienestar",
    "books & stationery": "libros papelería",
    "experiences": "experiencias",
    "games & puzzles": "juegos rompecabezas",
    "home & living": "hogar casa",
    "jewellery": "joyería",
    "kids": "niños infantil",
    "kitchen & dining": "cocina comedor",
    "outdoor & travel": "aire libre viaje",
    "pets": "mascotas",
    "tech & gadgets": "tecnología gadgets",
    # Subcategories
    "audio": "audio altavoces auriculares",
    "bags": "bolsos mochilas",
    "bar": "coctelería bar",
    "bath": "baño",
    "board games": "juegos de mesa",
    "books": "libros",
    "bracelets": "pulseras",
    "candles": "velas",
    "cats": "gatos",
    "classic games": "juegos clásicos",
    "coffee": "café",
    "cookware": "ollas sartenes utensilios",
    "craft": "manualidades",
    "creative": "creativo",
    "decor": "decoración",
    "desk": "escritorio oficina",
    "dogs": "perros",
    "drinkware": "botellas tazas vasos",
    "earrings": "pendientes",
    "fitness": "fitness ejercicio deporte",
    "fragrance": "fragancia perfume",
    "garden": "jardín",
    "gift cards": "tarjetas regalo",
    "grooming": "aseo afeitado",
    "home tech": "domótica hogar",
    "knives": "cuchillos",
    "lighting": "iluminación lámparas",
    "mobile": "móvil teléfono",
    "necklaces": "collares",
    "outdoor": "aire libre camping",
    "paper": "papel cuadernos",
    "pens": "bolígrafos plumas",
    "photo": "fotografía cámara",
    "puzzles": "rompecabezas puzzles",
    "reading": "lectura ebook",
    "recovery": "recuperación masaje",
    "rings": "anillos",
    "serving": "servir bandejas",
    "skincare": "cuidado piel facial",
    "sleep": "sueño dormir descanso",
    "storage": "almacenaje organización",
    "tableware": "vajilla",
    "tea": "té infusiones",
    "textiles": "textiles mantas",
    "toys": "juguetes",
    "vases": "jarrones",
    "watches": "relojes",
    "wearable": "wearable actividad",
}


def search_terms_es(*values: str | None) -> str:
    """Spanish search words for English taxonomy labels. Unknown labels contribute
    nothing rather than a guess - the same contract as the colour glossary."""
    return " ".join(SEARCH_TERMS_ES.get(v.casefold(), "") for v in values if v)


def translate_color(value: str | None) -> str | None:
    if not value:
        return None
    return COLOR_ES.get(value.casefold(), value)


def translate_material(value: str | None) -> str | None:
    """Split a compound value on ',', translate each token, rejoin.

    Any token outside the glossary is left in English rather than guessed - the same
    "absent is fine" contract as the rest of the translation system. Only the first
    letter of the result is capitalised, matching how a compound reads naturally in
    Spanish ("Acero, roble") rather than every word ("Acero, Roble").
    """
    if not value:
        return None
    parts = [part.strip() for part in value.split(",")]
    translated = [MATERIAL_WORD_ES.get(part.casefold(), part) for part in parts]
    joined = ", ".join(translated)
    return joined[:1].upper() + joined[1:] if joined else joined
