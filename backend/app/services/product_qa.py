"""
Product QA Validator — Automatic quality checks before review.

Runs after all enrichment (AI extraction, post-processing, image search, pricing)
and generates qa_warnings for each product. These warnings are displayed in the
review UI to help the user catch and fix issues before pushing to Shopify.

Each warning has:
  - level: "error" (blocking) | "warning" (should fix) | "info" (optional)
  - code: machine-readable identifier (e.g. "title_too_short")
  - message: human-readable Danish message for the review UI
  - field: which product field is affected
"""

import logging
import re

# One definition of "too far apart", shared with the merge policy that applies
# the same rule when an order confirmation is linked outside the pipeline.
from app.services.order_matching import COST_TOLERANCE

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# QA Warning helper
# ═══════════════════════════════════════════════

def _warn(level: str, code: str, field: str, message: str) -> dict:
    return {"level": level, "code": code, "field": field, "message": message}


# ══���═════════���══════════════════════════════════
# Title checks
# ═══════════���═══════════════════════════════════

_COMMON_COLOR_WORDS = {
    "sort", "hvid", "blå", "rød", "grå", "grøn", "brun", "beige", "creme",
    "navy", "black", "white", "blue", "red", "grey", "green", "brown",
    "off white", "dark blue", "light blue", "dark grey", "light grey",
    "cognac", "camel", "olive", "burgundy", "pink", "orange", "yellow",
    "sand", "taupe", "charcoal", "cream", "ivory", "khaki", "rust",
}

# Iconic product numbers that are legitimate in titles
_LEGITIMATE_PRODUCT_NUMBERS = {"501", "505", "511", "514", "527", "502", "1460", "1461", "574", "990"}


def _check_title(p: dict) -> list[dict]:
    warnings = []
    title = (p.get("title") or "").strip()
    style_code = (p.get("style_code") or "").strip()

    if not title:
        warnings.append(_warn("error", "title_missing", "title", "Produktet mangler titel"))
        return warnings

    title_lower = title.lower()

    # ── Critical: Title is just a number (= SKU as title) ──
    if re.match(r'^\d+$', title):
        warnings.append(_warn("error", "title_is_number", "title",
                               f"Titlen er et tal: \"{title}\" — mangler produktnavn"))
        return warnings  # No point checking further — title is fundamentally broken

    # ── Critical: Title looks like a code pattern (e.g. "AB-1234", "FW_2025") ──
    if re.match(r'^[A-Z0-9]{2,}[-_][A-Z0-9]{2,}', title):
        warnings.append(_warn("error", "title_looks_like_code", "title",
                               f"Titlen ligner en kode: \"{title}\""))
        return warnings

    # ── Warning: Title too short (< 5 chars is suspicious) ──
    if len(title) < 5:
        warnings.append(_warn("warning", "title_too_short", "title",
                               f"Titlen er meget kort: \"{title}\""))

    # ── Warning: Title starts with a long number (likely SKU prefix leak) ──
    # Only flag if not already caught as "is_number"
    if re.match(r'^\d{4,}', title):
        warnings.append(_warn("warning", "title_starts_with_number", "title",
                               f"Titlen starter med et tal — mulig SKU i titel"))

    # ── Warning: Title contains the style_code verbatim ──
    if style_code and len(style_code) >= 3:
        if style_code.lower() in title_lower:
            warnings.append(_warn("warning", "title_contains_sku", "title",
                                   f"Titlen indeholder SKU \"{style_code}\""))

    # ── Warning: Title is just a color name (too generic) ──
    if title_lower in _COMMON_COLOR_WORDS:
        warnings.append(_warn("warning", "title_is_just_color", "title",
                               f"Titlen er kun et farvenavn: \"{title}\""))

    # ── Warning: Title is ALL CAPS (bad for Shopify SEO) ──
    if len(title) > 3 and title == title.upper() and re.search(r'[A-Z]', title):
        warnings.append(_warn("warning", "title_all_caps", "title",
                               "Titlen er kun store bogstaver — bør have normal casing"))

    # ── Info: Title contains numeric color codes (e.g. "Jacket 900") ──
    numbers_in_title = re.findall(r'\b\d{3,4}\b', title)
    for num in numbers_in_title:
        if num not in _LEGITIMATE_PRODUCT_NUMBERS:
            warnings.append(_warn("info", "title_has_numeric_code", "title",
                                   f"Titlen indeholder tal \"{num}\" — mulig farvekode"))
            break

    return warnings


# ══════════════��════════════════════════════════
# Description checks
# ═══════════════��═══════════════════════════════

_FORBIDDEN_DESCRIPTION_PHRASES = [
    # AI hallucination phrases (invoice-referencing)
    "fremgår ikke", "kan ikke udledes", "ikke tilgængelig", "ikke oplyst",
    "ikke specificeret", "ikke angivet", "ikke kendt",
    "fra fakturaen", "af fakturaen", "på fakturaen",
    # Generic marketing fluff
    "perfekt til", "ideel til", "elegant", "raffineret", "tidløs",
    "statement piece", "versatile", "effortless", "timeless", "sophisticated",
    "must-have", "go-to", "elevate your", "upgrade your",
    # Placeholder-like phrases
    "beskrivelse mangler", "ingen beskrivelse", "to be added",
]

# Common Danish words — if description_da contains mostly English, flag it
_DANISH_MARKERS = {"med", "til", "fra", "har", "kan", "som", "der", "denne", "dette", "også", "eller"}
_ENGLISH_MARKERS = {"with", "the", "and", "for", "this", "that", "from", "has", "can", "also", "which"}


def _check_description(p: dict) -> list[dict]:
    warnings = []
    details = (p.get("details") or p.get("description_da") or "").strip()
    details_en = (p.get("details_en") or p.get("description_en") or "").strip()
    title = (p.get("title") or "").strip()
    vendor = (p.get("vendor") or "").strip()
    style_code = (p.get("style_code") or "").strip()

    # ── Danish description ──
    if not details:
        warnings.append(_warn("warning", "description_da_missing", "details",
                               "Dansk beskrivelse mangler"))
    elif len(details) < 30:
        warnings.append(_warn("warning", "description_da_too_short", "details",
                               f"Dansk beskrivelse er for kort ({len(details)} tegn)"))
    else:
        details_lower = details.lower()
        details_words = set(re.findall(r'\b[a-zæøå]+\b', details_lower))

        # Check for forbidden phrases
        for phrase in _FORBIDDEN_DESCRIPTION_PHRASES:
            if phrase in details_lower:
                warnings.append(_warn("warning", "description_da_forbidden_phrase", "details",
                                       f"Beskrivelsen indeholder \"{phrase}\""))
                break

        # Check if description just repeats the title at the start
        if title and len(title) > 3 and title.lower() in details_lower[:len(title) + 20]:
            warnings.append(_warn("info", "description_repeats_title", "details",
                                   "Beskrivelsen gentager titlen"))

        # Check for vendor name in description (only for vendors with 3+ chars to avoid false positives)
        if vendor and len(vendor) >= 3:
            # Use word boundary to avoid "NN" matching "innocent" etc.
            if re.search(r'\b' + re.escape(vendor.lower()) + r'\b', details_lower):
                warnings.append(_warn("warning", "description_contains_vendor", "details",
                                       f"Beskrivelsen nævner brandet \"{vendor}\""))

        # Check for SKU in description
        if style_code and len(style_code) >= 3 and style_code.lower() in details_lower:
            warnings.append(_warn("warning", "description_contains_sku", "details",
                                   f"Beskrivelsen indeholder SKU \"{style_code}\""))

        # Check for only 1 sentence (too sparse)
        sentence_count = len(re.findall(r'[.!?]\s', details + " "))
        if sentence_count < 2:
            warnings.append(_warn("info", "description_da_one_sentence", "details",
                                   "Beskrivelsen har kun 1 sætning — overvej at tilføje detaljer"))

        # Language mismatch: Danish description seems to be in English
        en_count = len(details_words & _ENGLISH_MARKERS)
        da_count = len(details_words & _DANISH_MARKERS)
        if en_count >= 3 and da_count == 0:
            warnings.append(_warn("warning", "description_da_wrong_language", "details",
                                   "Dansk beskrivelse ser ud til at være på engelsk"))

    # ── English description ──
    if not details_en:
        warnings.append(_warn("info", "description_en_missing", "details_en",
                               "Engelsk beskrivelse mangler"))
    elif len(details_en) < 30:
        warnings.append(_warn("info", "description_en_too_short", "details_en",
                               f"Engelsk beskrivelse er for kort ({len(details_en)} tegn)"))
    else:
        # Language mismatch: English description seems to be in Danish
        en_words = set(re.findall(r'\b[a-zæøå]+\b', details_en.lower()))
        da_count = len(en_words & _DANISH_MARKERS)
        en_count = len(en_words & _ENGLISH_MARKERS)
        if da_count >= 3 and en_count == 0:
            warnings.append(_warn("warning", "description_en_wrong_language", "details_en",
                                   "Engelsk beskrivelse ser ud til at være på dansk"))

    return warnings


# ═══════════════════════════════════════════════
# Image checks
# ═══════════════════════════════════════════════

def _check_images(p: dict) -> list[dict]:
    warnings = []
    images = p.get("images") or []
    image_source = (p.get("image_source") or "").strip()

    if not images:
        # Only emit ONE warning for missing images — pick the most specific reason
        if image_source.startswith("rejected:"):
            source = image_source.replace("rejected:", "")
            warnings.append(_warn("warning", "images_rejected", "images",
                                   f"Billeder fra {source} blev afvist af Vision-verificering"))
        elif image_source == "none":
            warnings.append(_warn("warning", "images_source_none", "images",
                                   "Billedsøgningen fandt ingen resultater — upload manuelt"))
        else:
            warnings.append(_warn("warning", "images_missing", "images",
                                   "Ingen billeder fundet — upload manuelt"))
    else:
        if len(images) == 1:
            warnings.append(_warn("info", "images_only_one", "images",
                                   "Kun 1 billede fundet — overvej at tilføje flere"))

        # Check for verify errors even when images exist (images might be wrong)
        if image_source.startswith("verify_error:"):
            warnings.append(_warn("info", "images_verify_error", "images",
                                   "Vision-verificering fejlede — billederne kan være forkerte"))

    return warnings


# ���═══════════��══════════════════════════════════
# Variant / size checks
# ═���══════════════════════��══════════════════════

def _check_variants(p: dict) -> list[dict]:
    warnings = []
    variants = p.get("variants") or []

    if not variants:
        warnings.append(_warn("error", "variants_missing", "variants",
                               "Ingen varianter/størrelser fundet"))
        return warnings

    # All variants have 0 quantity
    total_qty = sum(v.get("quantity", 0) for v in variants)
    if total_qty == 0:
        warnings.append(_warn("warning", "variants_zero_quantity", "variants",
                               "Alle varianter har 0 stk — tjek om antal er korrekt"))

    # Per-variant checks
    empty_sizes = 0
    missing_ean = 0
    for v in variants:
        size = (v.get("size") or "").strip()
        ean = (v.get("ean") or "").strip()

        if not size:
            empty_sizes += 1
        elif len(size) > 10:
            warnings.append(_warn("info", "variant_long_size", "variants",
                                   f"Usædvanlig størrelse: \"{size}\""))

        if not ean:
            missing_ean += 1

    if empty_sizes > 0:
        warnings.append(_warn("warning", "variant_empty_size", "variants",
                               f"{empty_sizes} variant(er) mangler størrelse"))

    # EAN check — only flag if ALL variants are missing EAN (some brands don't provide)
    if missing_ean == len(variants) and len(variants) > 0:
        warnings.append(_warn("info", "variants_no_ean", "variants",
                               "Ingen varianter har EAN/stregkode"))

    # Duplicate sizes
    sizes = [v.get("size", "") for v in variants]
    if len(sizes) != len(set(sizes)):
        seen = set()
        dupes = set()
        for s in sizes:
            if s in seen:
                dupes.add(s)
            seen.add(s)
        warnings.append(_warn("warning", "variants_duplicate_sizes", "variants",
                               f"Duplikerede størrelser: {', '.join(sorted(dupes))}"))

    return warnings


# ═════��═════════════════════════════��═══════════
# Price checks
# ═════��════════════��════════════════════════���═══

def _check_pricing(p: dict, eur_rate: float = 7.46) -> list[dict]:
    warnings = []
    cost = p.get("cost_price_eur") or 0
    retail = p.get("retail_price_dkk") or 0

    if not cost or cost <= 0:
        warnings.append(_warn("error", "price_missing", "cost_price_eur",
                               "Kostpris mangler eller er 0"))
    else:
        if cost > 5000:
            warnings.append(_warn("info", "price_very_high", "cost_price_eur",
                                   f"Kostpris er usædvanlig høj: ���{cost:.0f}"))
        elif cost < 1:
            warnings.append(_warn("warning", "price_very_low", "cost_price_eur",
                                   f"Kostpris er usædvanlig lav: €{cost:.2f}"))

    if retail and cost and cost > 0:
        cost_dkk = cost * eur_rate
        markup = retail / cost_dkk
        if markup > 6:
            warnings.append(_warn("info", "price_high_markup", "retail_price_dkk",
                                   f"Markup virker høj: {markup:.1f}x"))
        elif 0 < markup < 1.5:
            warnings.append(_warn("warning", "price_low_markup", "retail_price_dkk",
                                   f"Markup virker lav: {markup:.1f}x — tjek om pris er korrekt"))

    return warnings


# ══════════���═════════════════��══════════════════
# Color checks
# ══════════════��════════════════════════════════

def _check_cost_vs_order_confirmation(p: dict) -> list[dict]:
    """
    Compare what the invoice charged against what the order confirmation quoted.

    A gap here means a price error, a currency mix-up (a DKK figure read as EUR
    lands about 7x out) or a supplier changing the price after the order — all
    worth catching before the product reaches Shopify with the wrong margin.

    Reads the two figures the merge stashed on the product. cost_price_eur
    itself is no use: the merge policy overwrites it with the confirmation's
    price, so comparing it against the confirmation would always give zero.
    """
    invoice_cost = p.get("_invoice_cost_price_eur")
    order_cost = p.get("_order_confirmation_wholesale_price")

    if invoice_cost is None or order_cost is None:
        return []
    try:
        invoice_cost = float(invoice_cost)
        order_cost = float(order_cost)
    except (TypeError, ValueError):
        return []

    denominator = max(abs(invoice_cost), abs(order_cost))
    if denominator == 0:
        return []

    deviation = abs(invoice_cost - order_cost) / denominator
    if deviation <= COST_TOLERANCE:
        return []

    return [_warn(
        "warning",
        "cost_mismatch_order_confirmation",
        "cost_price_eur",
        f"Prisafvigelse: faktura €{invoice_cost:.2f}, "
        f"ordrebekræftelse €{order_cost:.2f} ({deviation * 100:.1f}%)",
    )]


def _check_color(p: dict) -> list[dict]:
    warnings = []
    color = (p.get("color") or "").strip()
    color_original = (p.get("color_original") or "").strip()

    if not color and not color_original:
        warnings.append(_warn("info", "color_missing", "color",
                               "Ingen farve angivet"))

    # Color is just a number (e.g. "900" — unresolved color code)
    if color and re.match(r'^\d+$', color):
        warnings.append(_warn("warning", "color_is_code", "color",
                               f"Farve er en kode: \"{color}\" — bør oversættes til farvenavn"))

    # Color looks like a hex code
    if color and re.match(r'^#?[0-9a-fA-F]{6}$', color):
        warnings.append(_warn("warning", "color_is_hex", "color",
                               f"Farve er en hex-kode: \"{color}\" — bør oversættes"))

    return warnings


# ═══════════════════════════════════════════════
# Handle / SEO checks
# ═══════════════════════════════════════════════

def _check_handle(p: dict) -> list[dict]:
    warnings = []
    handle = (p.get("handle") or "").strip()
    title = (p.get("title") or "").strip()

    if not handle and title:
        warnings.append(_warn("info", "handle_missing", "handle",
                               "URL-slug (handle) mangler — genereres automatisk af Shopify"))

    # Handle contains numbers that look like SKU
    if handle and re.match(r'^[\d-]+$', handle):
        warnings.append(_warn("warning", "handle_is_numeric", "handle",
                               f"URL-slug er kun tal: \"{handle}\" — dårligt for SEO"))

    return warnings


# ═════════════════════════════════════════════��═
# Misc checks
# ═════════════���═══════════════════════��═════════

def _check_misc(p: dict) -> list[dict]:
    warnings = []

    # Missing vendor
    if not (p.get("vendor") or "").strip():
        warnings.append(_warn("error", "vendor_missing", "vendor", "Brand/vendor mangler"))

    # Missing product type
    if not (p.get("product_type") or "").strip():
        warnings.append(_warn("info", "product_type_missing", "product_type",
                               "Produkttype mangler"))

    # Missing material (not critical but useful for Shopify)
    if not (p.get("material") or "").strip():
        warnings.append(_warn("info", "material_missing", "material",
                               "Materiale mangler"))

    # Missing gender (important for fashion products)
    if not (p.get("gender") or "").strip():
        warnings.append(_warn("info", "gender_missing", "gender",
                               "Køn/gender mangler"))

    return warnings


# ══════════════════════���════════════════════════
# Cross-field checks (relations between fields)
# ══════════��═══════════════════════════��════════

def _check_cross_field(p: dict) -> list[dict]:
    warnings = []
    title = (p.get("title") or "").strip().lower()
    color = (p.get("color") or "").strip().lower()
    product_type = (p.get("product_type") or "").strip().lower()

    # Title = color + product_type exactly (too generic, likely auto-generated fallback)
    if title and color and product_type:
        generic_combos = [
            f"{product_type} {color}",
            f"{color} {product_type}",
        ]
        if title in generic_combos:
            warnings.append(_warn("info", "title_is_generic_combo", "title",
                                   "Titlen er kun type + farve — overvej et mere specifikt navn"))

    return warnings


# ═════���════════════���════════════════════════════
# Main QA function
# ═══════════════════════════════════════════════

def validate_product(product: dict, eur_rate: float = 7.46) -> list[dict]:
    """
    Run all quality checks on a single product dict.
    Returns list of warning dicts with level, code, field, message.

    Args:
        product: Product dict from AI extraction pipeline
        eur_rate: EUR/DKK exchange rate for markup calculation
    """
    warnings = []
    warnings.extend(_check_title(product))
    warnings.extend(_check_description(product))
    warnings.extend(_check_images(product))
    warnings.extend(_check_variants(product))
    warnings.extend(_check_pricing(product, eur_rate=eur_rate))
    warnings.extend(_check_cost_vs_order_confirmation(product))
    warnings.extend(_check_color(product))
    warnings.extend(_check_handle(product))
    warnings.extend(_check_misc(product))
    warnings.extend(_check_cross_field(product))
    return warnings


def validate_products(products: list[dict], eur_rate: float = 7.46) -> list[dict]:
    """
    Run QA validation on a list of products. Adds 'qa_warnings' key to each product.
    Returns the same list with warnings added.

    Args:
        products: List of product dicts from AI extraction pipeline
        eur_rate: EUR/DKK exchange rate for markup calculation
    """
    for p in products:
        warnings = validate_product(p, eur_rate=eur_rate)

        # Carry over warnings raised earlier in the pipeline — the order
        # confirmation merge adds its own. Assigning outright would silently
        # drop them, since QA runs after the merge. Deduplicated on
        # (code, field) so a check that also runs here is not listed twice.
        seen = {(w.get("code"), w.get("field")) for w in warnings}
        for existing in p.get("qa_warnings") or []:
            key = (existing.get("code"), existing.get("field"))
            if key not in seen:
                seen.add(key)
                warnings.append(existing)

        p["qa_warnings"] = warnings

        # Log summary per product (only if issues found)
        error_count = sum(1 for w in warnings if w["level"] == "error")
        warn_count = sum(1 for w in warnings if w["level"] == "warning")
        info_count = sum(1 for w in warnings if w["level"] == "info")
        if error_count or warn_count:
            title = p.get("title", p.get("style_code", "unknown"))
            logger.info(
                f"[QA] {title}: {error_count} errors, {warn_count} warnings, {info_count} info"
            )

    # Summary stats
    total_errors = sum(
        sum(1 for w in p.get("qa_warnings", []) if w["level"] == "error")
        for p in products
    )
    total_warnings = sum(
        sum(1 for w in p.get("qa_warnings", []) if w["level"] == "warning")
        for p in products
    )
    logger.info(
        f"[QA] Validated {len(products)} products: "
        f"{total_errors} total errors, {total_warnings} total warnings"
    )

    return products
