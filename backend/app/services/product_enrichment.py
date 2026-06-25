"""
Product data helpers — pricing, handles, tags, descriptions.
Ported from app.py — calculate_retail_price, map_type_danish, make_handle,
sort_sizes, build_tags, build_description_da, build_description_en.
"""

import re

from app.services.ai_extractor import _get_fallback_description


# ---------------------------------------------------------------------------
# Constants (ported from app.py globals)
# ---------------------------------------------------------------------------

DEFAULT_EUR_TO_DKK = 7.46
DEFAULT_MARKUP = 2.5

TYPE_MAP_DA: dict[str, str] = {
    "trouser": "Bukser", "pants": "Bukser", "pantalon": "Bukser",
    "trousers": "Bukser", "shorts": "Shorts", "short": "Shorts",
    "bermuda": "Shorts", "shirt": "Skjorter", "chemise": "Skjorter",
    "t-shirt": "T-Shirts", "tee": "T-Shirts", "knit": "Strik",
    "knitwear": "Strik", "pull": "Strik", "pullover": "Strik",
    "sweater": "Strik", "cardigan": "Strik", "jacket": "Jakker",
    "coat": "Jakker", "blazer": "Blazere", "dress": "Kjoler",
    "skirt": "Nederdele", "top": "Toppe", "blouse": "Bluser",
    "hoodie": "Hoodies", "hoodies": "Hoodies",
    "sweatshirt": "Sweatshirts", "sweatshirts": "Sweatshirts",
    "sweatpants": "Bukser", "sweatpant": "Bukser",
    "jogger": "Bukser", "joggers": "Bukser",
    "trackpant": "Bukser", "trackpants": "Bukser", "track pants": "Bukser",
    "chinos": "Bukser", "chino": "Bukser",
    "jeans": "Bukser", "denim": "Bukser",
    "leggings": "Bukser", "legging": "Bukser",
    "vest": "Veste", "polo": "Poloer",
    "overshirt": "Skjorter", "gilet": "Veste",
    "parka": "Jakker", "anorak": "Jakker", "windbreaker": "Jakker",
    "bomber": "Jakker", "down jacket": "Jakker",
    "tank top": "Toppe", "tank": "Toppe", "camisole": "Toppe",
    "bodysuit": "Toppe", "jumpsuit": "Kjoler",
    # Shoes
    "sneaker": "Sneakers", "sneakers": "Sneakers",
    "sandal": "Sandaler", "sandals": "Sandaler",
    "boot": "Støvler", "boots": "Støvler",
    "loafer": "Loafers", "loafers": "Loafers",
    "shoe": "Sko", "shoes": "Sko",
    # Bags
    "bag": "Tasker", "tote": "Tasker", "backpack": "Rygsække",
    "wallet": "Punge", "purse": "Punge",
    "crossbody": "Crossbody tasker",
    # Accessories
    "scarf": "Tørklæder", "hat": "Hatte", "cap": "Kasketter",
    "belt": "Bælter", "gloves": "Handsker",
    "sunglasses": "Solbriller", "jewellery": "Smykker",
    "perfume": "Parfume", "fragrance": "Parfume",
}

CLOTHING_TYPES: set[str] = {
    "Bukser", "Shorts", "Skjorter", "T-Shirts", "Strik", "Jakker",
    "Blazere", "Kjoler", "Nederdele", "Toppe", "Bluser", "Hoodies",
    "Sweatshirts", "Veste", "Poloer",
}

SHOE_TYPES: set[str] = {"Sneakers", "Sandaler", "Støvler", "Loafers", "Sko"}
BAG_TYPES: set[str] = {"Tasker", "Rygsække", "Punge", "Crossbody tasker"}

# Default weights in grams by product type (realistic estimates for shipping)
DEFAULT_WEIGHT_GRAMS: dict[str, int] = {
    "Bukser": 450, "Shorts": 300, "Skjorter": 250, "T-Shirts": 200,
    "Strik": 400, "Jakker": 800, "Blazere": 600, "Kjoler": 400,
    "Nederdele": 300, "Toppe": 180, "Bluser": 220, "Hoodies": 500,
    "Sweatshirts": 450, "Veste": 350, "Poloer": 250,
    "Sneakers": 900, "Sandaler": 500, "Støvler": 1200, "Loafers": 700, "Sko": 800,
    "Tasker": 600, "Rygsække": 700, "Punge": 150, "Crossbody tasker": 400,
    "Tørklæder": 120, "Bælter": 150, "Hatte": 100, "Kasketter": 80,
    "Handsker": 80, "Solbriller": 50, "Smykker": 30, "Parfume": 300,
}


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def calculate_retail_price(
    cost_eur: float,
    rate: float = DEFAULT_EUR_TO_DKK,
    markup: float = DEFAULT_MARKUP,
) -> float:
    """Calculate retail price: cost x rate x markup, rounded to nearest 50 DKK.
    Includes sanity checks for unrealistic prices."""
    if cost_eur <= 0:
        return 0  # Will be flagged in review
    if cost_eur > 5000:
        # Sanity: cost > 5000 EUR is suspicious — might be a parsing error
        pass  # Still calculate but UI will warn
    raw = cost_eur * rate * markup
    rounded = round(raw / 50) * 50
    if rounded % 100 == 50 and abs(raw - round(raw / 100) * 100) < 30:
        rounded = round(raw / 100) * 100
    return max(rounded, 50)


def map_type_danish(raw_type: str) -> str:
    """Map an English product type to its Danish equivalent."""
    key = raw_type.lower().strip()
    return TYPE_MAP_DA.get(key, raw_type.title())


def make_handle(vendor: str, title: str) -> str:
    """Generate a Shopify-compatible URL handle from vendor + title."""
    raw = f"{vendor} {title}"
    handle = raw.lower().strip()
    # Transliterate accented characters (French, etc.)
    char_map = {
        "æ": "ae", "ø": "oe", "å": "aa",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "ù": "u", "û": "u", "ü": "u",
        "ô": "o", "ö": "o", "ò": "o",
        "î": "i", "ï": "i", "ì": "i",
        "ç": "c", "ñ": "n", "ß": "ss",
    }
    for char, replacement in char_map.items():
        handle = handle.replace(char, replacement)
    handle = re.sub(r"[^a-z0-9\s-]", "", handle)
    handle = re.sub(r"\s+", "-", handle)
    handle = re.sub(r"-+", "-", handle)
    return handle.strip("-")


def sort_sizes(variants: list[dict]) -> list[dict]:
    """Sort variants by size in logical order (XS->XXL for letters, ascending for numbers).
    Handles: letter sizes, numeric sizes, combined sizes (M/S, L/M), and One Size.
    """
    SIZE_ORDER = {
        "one size": 0, "os": 0, "one": 0,
        "xxxs": 1, "xxs": 2, "xs": 3,
        "s": 4, "m": 5, "l": 6,
        "xl": 7, "xxl": 8, "xxxl": 9,
        # Combined/unisex sizes
        "xs/s": 3.5, "s/m": 4.5, "m/l": 5.5, "l/xl": 6.5, "xl/xxl": 7.5,
        "m/s": 4.5, "l/m": 5.5, "xl/l": 6.5,
    }

    def _size_key(variant):
        size = variant.get("size", "").strip()
        size_lower = size.lower()

        # Check direct match in order map
        if size_lower in SIZE_ORDER:
            return (0, SIZE_ORDER[size_lower], 0)

        # Try numeric (36, 38, 40, 42, 44, 46, 48, 50 etc.)
        try:
            num = float(size.replace(",", "."))
            return (1, num, 0)
        except (ValueError, TypeError):
            pass

        # Try extracting number from mixed format ("EU 42", "US 10")
        num_match = re.search(r'(\d+(?:[.,]\d+)?)', size)
        if num_match:
            try:
                return (1, float(num_match.group(1).replace(",", ".")), 0)
            except ValueError:
                pass

        # Unknown — sort alphabetically at the end
        return (2, 0, size)

    return sorted(variants, key=_size_key)


def build_tags(
    product: dict,
    existing_tags: list[str] | None = None,
) -> list[str]:
    """Build STROM tags list — selective, no redundant tags.

    Parameters
    ----------
    product : dict
        Product data dict with keys like gender, vendor, product_type_da, season, ai_tags.
    existing_tags : list[str] | None
        Tags that already exist in the Shopify store (used to filter AI-suggested tags).
    """
    tags: list[str] = []

    # Gender tag — always "Men" or "Women", unisex gets both
    gender = product.get("gender", "").lower()
    if gender == "unisex":
        tags.extend(["Men", "Women"])
    elif gender in ("men", "menswear", "herrer", "male"):
        tags.append("Men")
    elif gender in ("women", "womenswear", "damer", "female"):
        tags.append("Women")

    # Brand tag
    vendor = product.get("vendor", "")
    if vendor:
        tags.append(vendor)

    # Product type tag (Danish)
    type_da = product.get("product_type_da", "")
    if type_da:
        tags.append(type_da)

    # "Toj" only for clothing categories
    if type_da in CLOTHING_TYPES:
        tags.append("Tøj")

    # Season tag (SS26, FW26, etc.)
    season = product.get("season", "")
    if season:
        tags.append(season)

    # Acne Studios exception
    if "acne" in vendor.lower():
        tags.append("acne-products")

    # Add ONLY AI-suggested tags that match existing store tags — be very selective
    # Filter out tags we already handle, English types, gender variants, materials, colors
    skip_tags = {
        # English product types — singular AND plural (we use Danish versions)
        "Shirt", "Shirts", "Trouser", "Trousers", "Pants", "Knit", "Knitwear",
        "Jacket", "Jackets", "Coat", "Coats", "Blazer", "Blazers",
        "Dress", "Dresses", "Skirt", "Skirts", "Top", "Tops", "Blouse", "Blouses",
        "Shorts", "Hoodie", "Hoodies", "Sweatshirt", "Sweatshirts",
        "Vest", "Vests", "Polo", "Polos",
        "Sneaker", "Sneakers", "Sandal", "Sandals", "Boot", "Boots",
        "Loafer", "Loafers", "Shoe", "Shoes",
        "Bag", "Bags", "Scarf", "Scarves", "Hat", "Hats", "Cap", "Caps",
        "Belt", "Belts", "Gloves", "Wallet", "Wallets",
        "T-Shirt", "T-shirt", "Tee", "Tees",
        "Accessories", "Accessory",
        # Danish product types (already set from product_type_da — don't duplicate)
        "Bukser", "Skjorter", "T-Shirts", "Strik", "Jakker", "Blazere",
        "Kjoler", "Nederdele", "Toppe", "Bluser", "Hoodies", "Sweatshirts",
        "Veste", "Poloer", "Sneakers", "Sandaler", "Støvler", "Loafers", "Sko",
        "Tasker", "Rygsække", "Punge", "Tørklæder", "Bælter", "Hatte",
        "Kasketter", "Handsker", "Solbriller", "Smykker", "Parfume",
        "Hættetrøjer", "Sweatshirts og hættetrøjer",
        # Gender variants (we already add "Men"/"Women" from gender field)
        "Womens", "Mens", "Women's", "Men's", "Womenswear", "Menswear",
        "Women", "Men", "Male", "Female", "Herrer", "Damer", "Unisex",
        # Materials — should NEVER be tags (they go in metafields/description)
        "Cotton", "100% Cotton", "Wool", "Silk", "Linen", "Polyester",
        "Nylon", "Cashmere", "Viscose", "Elastane", "Polyamide", "Leather",
        "Suede", "Denim", "Canvas", "Organic Cotton", "Merino",
        "Bomuld", "100% Bomuld", "Uld", "Silke", "Hør", "Kashmir",
        "Læder", "Ruskind",
        # Color names — should NEVER be tags (they go in Color metafield)
        "Sort", "Hvid", "Blå", "Rød", "Grå", "Grøn", "Brun", "Beige",
        "Gul", "Orange", "Rosa", "Lilla", "Navy", "Lyseblå", "Mørkegrå",
        "Black", "White", "Blue", "Red", "Grey", "Gray", "Green", "Brown",
        "Yellow", "Pink", "Purple", "Navy Blue", "Dark", "Light",
        # Generic tags that add no value
        "Clothing", "Fashion", "New", "Sale", "Premium", "Luxury",
        "Scandinavian", "Minimalist", "Classic", "Modern", "Casual",
        "Streetwear", "Designer", "Brand", "Collection",
    }
    # Also skip tags that are just the vendor name (already added above)
    skip_tags.add(vendor)

    ai_tags = product.get("ai_tags", [])
    existing = set(existing_tags or [])
    for t in ai_tags:
        # Skip if in blocklist (case-sensitive match)
        if t in skip_tags:
            continue
        # Skip if it's a case variant of a blocked tag
        if t.lower() in {s.lower() for s in skip_tags}:
            continue
        # Skip if it duplicates an already-added tag (case-insensitive)
        if t.lower() in {existing_t.lower() for existing_t in tags}:
            continue
        if t in existing and t not in tags:
            tags.append(t)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_tags: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return unique_tags


# ═══════════════════════════════════════════════
# SEO Keyword Validation — product-aware, brand-agnostic
# ═══════════════════════════════════════════════
# Shared validation used in BOTH extraction post-processing AND push-time.
# Instead of maintaining a hardcoded competitor list, this validates keywords
# against the product's OWN data — if a keyword doesn't relate to the product,
# it's rejected regardless of which brand or language it comes from.

# Words that are always allowed in keywords (Danish prepositions, articles, etc.)
_KW_STOP_WORDS = {
    "i", "med", "til", "fra", "og", "den", "det", "en", "et", "af",
    "for", "på", "ved", "over", "under", "uden",
}

# Foreign-language words that should never appear in Danish SEO keywords
_FOREIGN_NOISE = {
    # German
    "damen", "herren", "kinder", "frauen", "männer", "mädchen",
    "kaufen", "günstig", "billig", "bestellen", "angebot", "kleidung",
    "schuhe", "jacke", "hose", "hemd", "kleid", "mantel",
    # English commercial
    "buy", "cheap", "sale", "discount", "best", "review", "price",
    "shop", "store", "free shipping", "online", "order",
    # English fashion (should be Danish)
    "outfit", "style guide", "fashion", "clothing", "wear",
    "streetwear", "menswear", "womenswear",
}

# Danish fashion vocabulary — words that are VALID in Danish SEO keywords.
# Used for relevance checking: keywords should contain at least one word
# that relates to the product's actual properties.
_DANISH_FASHION_VOCAB = {
    # Product types (Danish)
    "bukser", "shorts", "skjorte", "skjorter", "t-shirt", "strik",
    "jakke", "jakker", "blazer", "kjole", "kjoler", "nederdel",
    "top", "toppe", "bluse", "bluser", "hoodie", "hoodies",
    "sweatshirt", "vest", "polo", "frakke", "overshirt",
    "jeans", "chinos", "leggings", "cardigan", "pullover",
    "sneakers", "sandaler", "støvler", "loafers", "sko",
    "taske", "tasker", "rygsæk", "pung", "tørklæde", "bælte",
    "hat", "kasket", "solbriller", "smykker", "parfume",
    "sweatpants", "sweatbukser", "joggingbukser",
    # Materials (Danish)
    "bomuld", "bomuldsblend", "uld", "silke", "hør", "læder",
    "ruskind", "denim", "kashmir", "nylon", "polyester",
    "viskose", "elastan", "fleece", "jersey", "satin",
    "strik", "frotté", "twill", "poplin", "canvas", "gore-tex",
    # Construction/details
    "slim", "regular", "relaxed", "oversized", "fitted",
    "knapper", "lynlås", "elastik", "snøre", "lommer",
    "krave", "manchet", "ribstrik", "pressefold",
    # Gender (Danish)
    "dame", "damer", "herre", "herrer", "kvinder", "mænd", "unisex",
    # Colors (Danish)
    "sort", "hvid", "blå", "rød", "grøn", "gul", "grå", "brun",
    "beige", "navy", "bordeaux", "lyserød", "lysegrå", "mørkegrå",
    "camel", "cognac", "sand", "oliven", "khaki", "creme",
    # Common compound parts
    "bomuldsshorts", "bomuldsskjorte", "uldjakke", "silkekjole",
    "ruskindstaske", "denimbukser", "læderbælte",
    # General fashion Danish + English fashion terms used in Danish context
    "designer", "premium", "klassisk", "moderne",
    "vintage", "studio", "studios", "collection", "essential",
    "sport", "active", "original", "originals", "selected",
}


def validate_seo_keywords(keywords: list[str], product: dict) -> list[str]:
    """Validate and filter SEO keywords against the product's own data.

    This function is BRAND-AGNOSTIC — it works for any vendor by checking
    keyword relevance against the product's structured fields rather than
    maintaining a competitor blacklist.

    Runs 5 checks:
    1. Foreign language detection (German, English commercial)
    2. Gender consistency (no "herre" for Women products)
    3. Vendor-name redundancy (vendor is already in meta title/description)
    4. Product relevance (keyword must relate to product's actual properties)
    5. Quality (no single-word keywords, no pure number keywords)
    """
    if not keywords:
        return keywords

    vendor = (product.get("vendor") or "").strip()
    vendor_lower = vendor.lower()
    gender = (product.get("gender") or "").lower()
    title_lower = (product.get("title") or "").lower()
    type_lower = (product.get("product_type") or product.get("product_type_da") or "").lower()
    material_lower = (product.get("material") or "").lower()
    color_lower = (product.get("color") or "").lower()

    # Build the product's vocabulary — words that describe THIS product
    product_words: set[str] = set()
    for field_val in [title_lower, type_lower, material_lower, color_lower]:
        product_words.update(w for w in field_val.split() if len(w) > 1)
    # Map product type to Danish vocabulary
    type_da = TYPE_MAP_DA.get(type_lower, "").lower()
    if type_da:
        product_words.update(type_da.split())
    product_words.discard("")

    valid: list[str] = []

    for kw in keywords:
        if not kw or not kw.strip():
            continue
        kw_lower = kw.lower().strip()
        kw_words = kw_lower.split()

        # Check 1: No foreign language words
        if any(fw in kw_words for fw in _FOREIGN_NOISE):
            continue

        # Check 2: Gender consistency
        if gender in ("women", "kvinder", "dame"):
            if any(g in kw_words for g in ("herre", "herrer", "mand", "mænd")):
                continue
        elif gender in ("men", "mænd", "herre"):
            if any(g in kw_words for g in ("dame", "damer", "kvinde", "kvinder")):
                continue

        # Check 3: Vendor name redundancy — vendor is already in meta title
        # and meta description opening, so repeating it wastes space and
        # often introduces misspellings (e.g., "Gabi gamel" instead of "GABI Gamél")
        if vendor_lower and len(vendor_lower) >= 2:
            # Full vendor string match (e.g. "acne studios" in "acne studios t-shirt")
            if vendor_lower in kw_lower:
                continue
            # Individual vendor word match for multi-word brands
            # (e.g. "acne" from "Acne Studios" in "acne t-shirt herre")
            # Only words 4+ chars to avoid false positives ("by", "co", "de")
            # Skip words that are common fashion vocabulary ("vintage", "studio")
            vendor_parts = [vp for vp in vendor_lower.split()
                           if len(vp) >= 4 and vp not in _DANISH_FASHION_VOCAB]
            if vendor_parts and any(vp in kw_words for vp in vendor_parts):
                continue

        # Check 4: Relevance — at least one non-stop word must connect to the product
        # or be a known Danish fashion term
        significant = [w for w in kw_words if w not in _KW_STOP_WORDS and len(w) > 1]
        if significant:
            has_relevance = any(
                w in product_words
                or w in _DANISH_FASHION_VOCAB
                or any(pw in w or w in pw for pw in product_words if len(pw) > 2)
                for w in significant
            )
            if not has_relevance:
                continue

        # Check 5: Quality — no single-character, no pure numbers
        if len(kw_lower) < 5:
            continue
        if re.match(r'^[\d\s]+$', kw_lower):
            continue

        valid.append(kw)

    return valid


def build_description_da(product: dict) -> str:
    """Build STROM product description in Danish — matches reference product format exactly.
    Format: One paragraph (title + vendor + details) + bullet list (Farve, Materiale).
    """
    title = product.get("title", "")
    vendor = product.get("vendor", "")
    color = product.get("color", "")
    material = product.get("material", "")
    details = product.get("details", "")
    type_da = product.get("product_type_da", "")

    lines: list[str] = []

    # Paragraph 1: Title + brand + physical details — one flowing paragraph
    detail_text = ""
    if details and len(details.strip()) > 10:
        detail_text = details
    else:
        detail_text = _get_fallback_description(type_da, color)

    # Safety: strip farve/materiale lines from detail_text to avoid duplicates
    # (they are added as separate bullet points below)
    detail_text = re.sub(r'Farve:\s*[^\n.]+[.\n]?\s*', '', detail_text, flags=re.IGNORECASE)
    detail_text = re.sub(r'Materiale:\s*[^\n.]+[.\n]?\s*', '', detail_text, flags=re.IGNORECASE)
    detail_text = re.sub(r'Mål:\s*[^\n.]+[.\n]?\s*', '', detail_text, flags=re.IGNORECASE)
    # Strip "Color: X" / "Material: Y" (English variants from AI)
    detail_text = re.sub(r'Colou?r:\s*[^\n.]+[.\n]?\s*', '', detail_text, flags=re.IGNORECASE)
    detail_text = re.sub(r'Material:\s*[^\n.]+[.\n]?\s*', '', detail_text, flags=re.IGNORECASE)

    # Safety: strip title duplication from detail_text BEFORE vendor strip.
    # The title is prepended below as "{title} fra {vendor}.", so the AI's text
    # should NOT contain the title at all. We strip ALL occurrences, not just leading.
    if title and detail_text:
        escaped_title = re.escape(title)
        # First: strip "Title fra Vendor." pattern anywhere (most common AI duplication)
        if vendor:
            detail_text = re.sub(
                rf'{escaped_title}\s+fra\s+{re.escape(vendor)}[\s.,;:!?\-–—]*',
                '', detail_text, flags=re.IGNORECASE
            ).strip()
        # Then: strip standalone title occurrences (with trailing punctuation/space)
        detail_text = re.sub(rf'{escaped_title}[\s.,;:!?\-–—]*', '', detail_text, flags=re.IGNORECASE).strip()

    # Safety: strip any remaining vendor references from detail_text
    # (the "fra {vendor}" prefix already handles branding)
    if vendor:
        detail_text = re.sub(rf'\s*er fra {re.escape(vendor)}\.?\s*', ' ', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(rf'\s*fra {re.escape(vendor)}\.?\s*', ' ', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(rf"\b{re.escape(vendor)}(?:'s|s)?\b", '', detail_text, flags=re.IGNORECASE)

    # Clean up double spaces, orphaned punctuation, and trailing whitespace
    detail_text = re.sub(r'\s{2,}', ' ', detail_text).strip()
    detail_text = re.sub(r'^\.\s*', '', detail_text)  # Remove leading dot
    detail_text = re.sub(r'\s*[,;]\s*$', '', detail_text)  # Remove trailing comma/semicolon

    lines.append(f"<p>{title} fra {vendor}. {detail_text}</p>")

    # Bullet list: Farve + Materiale + optional Maal for accessories
    bullet_items: list[str] = []
    if color and color.lower() not in ("ikke oplyst", "n/a", "unknown", ""):
        bullet_items.append(f"<li>Farve: {color}</li>")
    # Only show material if it's a real value (not "Ikke oplyst" etc.)
    material_clean = material.strip() if material else ""
    if material_clean and material_clean.lower() not in ("ikke oplyst", "n/a", "unknown", "ikke tilgængelig", ""):
        bullet_items.append(f"<li>Materiale: {material_clean}</li>")
    # Add dimensions bullet for accessory types (bags, wallets, backpacks etc.)
    accessory_types_with_dimensions = {"Tasker", "Punge", "Rygsække", "Crossbody tasker"}
    dimensions = product.get("dimensions", "")
    if type_da in accessory_types_with_dimensions and dimensions:
        bullet_items.append(f"<li>Mål: {dimensions}</li>")
    if bullet_items:
        lines.append("<ul>" + "".join(bullet_items) + "</ul>")

    return "\n".join(lines)


_TYPE_DA_TO_EN = {
    "Bukser": "Trousers", "Shorts": "Shorts", "Skjorter": "Shirts",
    "T-Shirts": "T-Shirts", "Strik": "Knitwear", "Jakker": "Jackets",
    "Blazere": "Blazers", "Kjoler": "Dresses", "Nederdele": "Skirts",
    "Toppe": "Tops", "Bluser": "Blouses", "Hoodies": "Hoodies",
    "Sweatshirts": "Sweatshirts", "Veste": "Vests", "Poloer": "Polos",
    "Sneakers": "Sneakers", "Sandaler": "Sandals", "Støvler": "Boots",
    "Loafers": "Loafers", "Sko": "Shoes", "Tasker": "Bags",
    "Rygsække": "Backpacks", "Punge": "Wallets", "Tørklæder": "Scarves",
    "Bælter": "Belts", "Hatte": "Hats", "Kasketter": "Caps",
    "Solbriller": "Sunglasses", "Smykker": "Jewellery", "Parfume": "Fragrance",
    "Huer": "Beanies",
}


def _translate_type(type_da: str) -> str:
    return _TYPE_DA_TO_EN.get(type_da, type_da)


def build_description_en(product: dict) -> str:
    """Build English translation of description — matches reference product format."""
    title = product.get("title", "")
    vendor = product.get("vendor", "")
    color = product.get("color", "")
    material = product.get("material", "")
    details_da = product.get("details", "")
    details_en = product.get("details_en", "")
    type_da = product.get("product_type_da", "")

    # Filter out "Ikke oplyst" (Danish "Not specified") from material
    _not_specified = ("ikke oplyst", "n/a", "unknown", "")
    if material and material.lower() in _not_specified:
        material = ""

    # Material translation DA->EN (use word boundary regex to avoid partial matches)
    material_en = material
    if material:
        translations = [
            ("bomuld", "cotton"), ("uld", "wool"), ("silke", "silk"),
            ("hør", "linen"), ("polyamid", "polyamide"), ("viskose", "viscose"),
            ("elastan", "elastane"), ("kashmir", "cashmere"), ("nylon", "nylon"),
            ("polyester", "polyester"), ("læder", "leather"), ("ruskind", "suede"),
            ("denim", "denim"), ("lærred", "canvas"), ("modal", "modal"),
            ("tencel", "tencel"), ("rayon", "rayon"), ("gore-tex", "Gore-Tex"),
            ("fleece", "fleece"), ("frotté", "terry"), ("satin", "satin"),
            ("chiffon", "chiffon"), ("jersey", "jersey"), ("twill", "twill"),
            ("poplin", "poplin"),
        ]
        for da, en in translations:
            material_en = re.sub(rf'\b{da}\b', en, material_en, flags=re.IGNORECASE)

    # Filter "Ikke oplyst" from details_en
    if details_en and "ikke oplyst" in details_en.lower():
        details_en = re.sub(r'(?i)ikke oplyst', '', details_en).strip()

    # Use details_en if available (AI now generates proper parallel English text)
    detail_text = details_en if details_en else ""
    if not detail_text or len(detail_text.strip()) < 10:
        # Minimal English fallback — short and factual rather than fake-detailed
        type_en = _translate_type(type_da)
        parts = []
        if type_en:
            parts.append(type_en)
        if material and material.lower() not in ("ikke oplyst", "n/a", "unknown", ""):
            # Quick material translation for fallback
            mat_en = material
            quick_translations = [
                ("bomuld", "cotton"), ("uld", "wool"), ("læder", "leather"),
                ("ruskind", "suede"), ("silke", "silk"), ("polyester", "polyester"),
            ]
            for da, en in quick_translations:
                mat_en = re.sub(rf'\b{da}\b', en, mat_en, flags=re.IGNORECASE)
            parts.append(f"in {mat_en}")
        if color and color.lower() not in ("ikke oplyst", "n/a", "unknown", ""):
            parts.append(f"in {color}")
        detail_text = " ".join(parts) + "." if parts else ""

    # Safety: strip title duplication from detail_text (same logic as Danish version)
    if title and detail_text:
        escaped_title = re.escape(title)
        if vendor:
            detail_text = re.sub(
                rf'{escaped_title}\s+from\s+{re.escape(vendor)}[\s.,;:!?\-–—]*',
                '', detail_text, flags=re.IGNORECASE
            ).strip()
        detail_text = re.sub(rf'{escaped_title}[\s.,;:!?\-–—]*', '', detail_text, flags=re.IGNORECASE).strip()

    # Safety: strip vendor references from detail_text
    if vendor:
        detail_text = re.sub(rf'\s*(?:is )?from {re.escape(vendor)}\.?\s*', ' ', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(rf"\b{re.escape(vendor)}(?:'s|s)?\b", '', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(r'\s{2,}', ' ', detail_text).strip()
        detail_text = re.sub(r'^\.\s*', '', detail_text)

    lines: list[str] = []

    # One paragraph: title + vendor + details
    lines.append(f"<p>{title} from {vendor}. {detail_text}</p>")

    # Bullet list: Color + Material
    bullet_items: list[str] = []
    if color and color.lower() not in ("ikke oplyst", "n/a", "unknown", ""):
        bullet_items.append(f"<li>Color: {color}</li>")
    if material_en:
        bullet_items.append(f"<li>Material: {material_en}</li>")
    if bullet_items:
        lines.append("<ul>" + "".join(bullet_items) + "</ul>")

    return "\n".join(lines)
