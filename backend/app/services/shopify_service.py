"""
Shopify service — Product push via GraphQL Admin API.

Ported from the Streamlit app's Shopify push logic. Handles full product
creation flow: product → variants → inventory → metafields → images →
publish → collections → translations.

All Streamlit dependencies (st.session_state, st.spinner) have been removed.
Shop domain and access token are passed as explicit parameters.
"""

import asyncio
import json
import logging
import re
import time

from app.core.config import get_settings
from app.services.product_enrichment import (
    CLOTHING_TYPES,
    build_tags,
    build_description_da,
    build_description_en,
    validate_seo_keywords,
)
from app.services.ai_extractor import _get_fallback_description

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Input sanitization
# ═══════════════════════════════════════════════

def _sanitize_text(text: str, max_length: int = 0) -> str:
    """Remove potentially dangerous content from text fields before Shopify push.

    Strips <script> tags, on* event handlers, and javascript: URLs.
    These should never appear in product data from PDF invoices,
    but defense-in-depth against malformed AI extraction output.
    """
    if not text:
        return text
    # Remove script tags and their content
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove on* event handlers from HTML tags
    text = re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bon\w+\s*=\s*\S+", "", text, flags=re.IGNORECASE)
    # Remove javascript: URLs
    text = re.sub(r"javascript\s*:", "", text, flags=re.IGNORECASE)
    # Remove data: URLs (potential XSS vector)
    text = re.sub(r"data\s*:\s*text/html", "", text, flags=re.IGNORECASE)
    if max_length > 0:
        text = text[:max_length]
    return text.strip()


# ═══════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════

DEFAULT_WEIGHT_GRAMS = {
    "Bukser": 450, "Shorts": 300, "Skjorter": 250, "T-Shirts": 200,
    "Strik": 400, "Jakker": 800, "Blazere": 600, "Kjoler": 400,
    "Nederdele": 300, "Toppe": 180, "Bluser": 220, "Hoodies": 500,
    "Sweatshirts": 450, "Veste": 350, "Poloer": 250,
    "Sneakers": 900, "Sandaler": 500, "Støvler": 1200, "Loafers": 700, "Sko": 800,
    "Tasker": 600, "Rygsække": 700, "Punge": 150, "Crossbody tasker": 400,
    "Tørklæder": 120, "Bælter": 150, "Hatte": 100, "Kasketter": 80,
    "Handsker": 80, "Solbriller": 50, "Smykker": 30, "Parfume": 300,
}


# Description + tag helpers imported from product_enrichment.py:
#   build_tags, build_description_da, build_description_en
# _get_fallback_description imported from ai_extractor.py


# ═══════════════════════════════════════════════
# Shopify Standardized Product Category Mapping
# ═══════════════════════════════════════════════
# Maps Danish product types to Shopify's standardized product taxonomy.
# Full path names as per Shopify's product category taxonomy.
# See: https://shopify.github.io/product-taxonomy/

_SHOPIFY_CATEGORY_MAP = {
    # Clothing
    "T-Shirts": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Skjorter": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Bluser": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Toppe": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Poloer": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Strik": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Bukser": "Apparel & Accessories > Clothing > Pants",
    "Shorts": "Apparel & Accessories > Clothing > Shorts",
    "Jakker": "Apparel & Accessories > Clothing > Outerwear > Coats & Jackets",
    "Blazere": "Apparel & Accessories > Clothing > Suits > Suit Jackets & Blazers",
    "Kjoler": "Apparel & Accessories > Clothing > Dresses",
    "Nederdele": "Apparel & Accessories > Clothing > Skirts",
    "Hoodies": "Apparel & Accessories > Clothing > Activewear > Hoodies",
    "Sweatshirts": "Apparel & Accessories > Clothing > Shirts & Tops",
    "Veste": "Apparel & Accessories > Clothing > Outerwear > Vests",
    # Shoes
    "Sneakers": "Apparel & Accessories > Shoes",
    "Sandaler": "Apparel & Accessories > Shoes",
    "Støvler": "Apparel & Accessories > Shoes",
    "Loafers": "Apparel & Accessories > Shoes",
    "Sko": "Apparel & Accessories > Shoes",
    # Bags
    "Tasker": "Apparel & Accessories > Handbags, Wallets & Cases",
    "Rygsække": "Apparel & Accessories > Handbags, Wallets & Cases",
    "Punge": "Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips",
    "Crossbody tasker": "Apparel & Accessories > Handbags, Wallets & Cases",
    # Accessories
    "Tørklæder": "Apparel & Accessories > Clothing Accessories > Scarves & Shawls",
    "Bælter": "Apparel & Accessories > Clothing Accessories > Belts",
    "Hatte": "Apparel & Accessories > Clothing Accessories > Hats",
    "Kasketter": "Apparel & Accessories > Clothing Accessories > Hats",
    "Huer": "Apparel & Accessories > Clothing Accessories > Hats",
    "Handsker": "Apparel & Accessories > Clothing Accessories > Gloves & Mittens",
    "Solbriller": "Apparel & Accessories > Clothing Accessories > Sunglasses",
    "Smykker": "Apparel & Accessories > Jewelry",
    "Parfume": "Health & Beauty > Personal Care > Cosmetics > Perfume & Cologne",
}


def _map_to_shopify_category(type_da: str) -> str:
    """Map Danish product type to Shopify standardized product category path."""
    return _SHOPIFY_CATEGORY_MAP.get(type_da, "")


# ═══════════════════════════════════════════════
# Main Shopify push function
# ═══════════════════════════════════════════════

async def push_product_to_shopify(
    shopify,  # ShopifyGraphQL instance
    product: dict,
    eur_rate: float,
    publications: list[dict],
    collections: list[dict],
    location_id: str,
    metafield_defs: list[dict] | None = None,
    existing_tags: list[str] | None = None,
    catalogs: list[dict] | None = None,
) -> dict:
    """
    Push a single product to Shopify using the GraphQL API.

    Flow: create product -> update variants -> inventory -> metafields ->
          images -> publish -> collections -> translations

    Args:
        shopify: ShopifyGraphQL client instance (created with shop_domain + access_token).
        product: Product dict with all fields (title, vendor, variants, etc.).
        eur_rate: EUR to DKK exchange rate.
        publications: List of Shopify publication dicts (id, name).
        collections: List of Shopify collection dicts (id, title, handle).
        location_id: Shopify location GID for inventory.
        metafield_defs: List of metafield definition dicts from the store.
        existing_tags: List of existing store tags (for AI tag matching in build_tags).

    Returns:
        Dict with product_id, title, variants count, and errors list.
    """
    push_start = time.time()
    # Sanitize all text inputs before sending to Shopify (defense-in-depth)
    title = _sanitize_text(product["title"], max_length=255)
    vendor = _sanitize_text(product["vendor"], max_length=255)
    type_da = _sanitize_text(product.get("product_type_da", ""), max_length=255)
    cost_eur = product.get("cost_price_eur", 0)
    cost_dkk = round(cost_eur * eur_rate, 2)
    retail_price = product.get("retail_price_dkk", 0)
    logger.info(f"Starting push for '{title}' by {vendor}")

    # Build tags
    tags = build_tags(product, existing_tags=existing_tags)

    # Add _test-import tag for test mode products (bypasses build_tags filter)
    if product.get("is_test"):
        tags.append("_test-import")

    # Description (Danish) — sanitize HTML output
    body_html = _sanitize_text(build_description_da(product))

    # ── SEO: Handle (URL slug) ──
    # Include color to avoid collisions (same product in multiple colors).
    color = product.get("color", "")
    material_info = product.get("material", "")
    handle = product.get("handle", "")
    if not handle:
        from app.services.product_enrichment import make_handle
        handle = make_handle(vendor, title)
    # Append color slug if not already in handle
    if color:
        color_slug = re.sub(r"[^a-z0-9]+", "-", color.lower().strip()).strip("-")
        if color_slug and color_slug not in handle:
            handle = f"{handle}-{color_slug}"

    # ── SEO: Meta title (max 70 chars) ──
    # Pattern: "Brand Product — specifik søgterm | STRØM"
    # Uses AI-generated seo_keywords to match real search intent
    seo_keywords_raw = product.get("seo_keywords", [])
    # Handle both enriched (list[dict]) and plain (list[str]) formats
    seo_keywords: list[str] = []
    for kw in seo_keywords_raw:
        if isinstance(kw, dict):
            seo_keywords.append(kw.get("keyword", ""))
        elif isinstance(kw, str):
            seo_keywords.append(kw)
    seo_keywords = [k for k in seo_keywords if k]

    # ── SEO keyword quality filtering ──
    # Primary: brand-agnostic validation from product_enrichment
    # (same function runs at extraction time — this is defense-in-depth at push)
    seo_keywords = validate_seo_keywords(seo_keywords, product)

    # Secondary: hardcoded brand filter — catches competitor names AND
    # other store brands that the relevance check can't detect
    vendor_lower = vendor.lower() if vendor else ""
    _COMPETITOR_BRANDS = {
        "adidas", "nike", "puma", "reebok", "new balance", "converse", "vans",
        "the north face", "patagonia", "columbia", "under armour",
        "gucci", "prada", "versace", "armani", "fendi", "balenciaga",
        "louis vuitton", "dior", "chanel", "hermes", "hermès", "moncler",
        "stone island", "off-white", "valentino", "bottega veneta", "celine",
        "tommy hilfiger", "ralph lauren", "calvin klein", "boss", "hugo boss",
        "lacoste", "burberry", "gant", "h&m", "zara", "mango", "uniqlo",
        "cos", "arket", "weekday", "massimo dutti", "jack & jones", "selected",
        "only", "vero moda", "pieces", "new yorker", "c&a", "primark",
        "boohoo", "asos", "shein", "samsøe samsøe", "wood wood",
        "norse projects", "ganni", "holzweiler", "by malene birger",
    }
    # Store's own brands — a keyword for product X should never mention brand Y
    _STORE_BRANDS = {
        "acne studios", "acne", "sunflower", "american vintage",
        "gabi", "gabi gamél", "comme des garcons", "comme des garçons",
        "cdg", "stüssy", "stussy", "our legacy", "a.p.c.", "apc",
        "ami paris", "ami", "maison kitsuné", "kitsune",
        "carhartt", "carhartt wip", "rick owens", "jil sander",
    }
    _ALL_BRANDS = _COMPETITOR_BRANDS | _STORE_BRANDS
    seo_keywords = [
        kw for kw in seo_keywords
        if not any(
            cb in kw.lower()
            for cb in _ALL_BRANDS
            if cb != vendor_lower and cb not in vendor_lower
        )
    ]

    seo_title_base = f"{vendor} {title}"
    if color and color.lower() not in title.lower():
        seo_title_base = f"{vendor} {title} {color}"

    seo_title = f"{seo_title_base} | STRØM"
    if len(seo_title) > 70:
        # Drop color if title is too long
        seo_title = f"{vendor} {title} | STRØM"
    if len(seo_title) > 70:
        seo_title = f"{title} | STRØM"
    if len(seo_title) > 70:
        seo_title = f"{title[:60]} | STRØM"

    # ── SEO: Meta description (max 320 chars) ──
    # Product-specific description built from actual product data + AI keywords.
    # Prioritises unique product details over generic CTA.
    seo_parts: list[str] = []

    # Opening: brand + product
    seo_parts.append(f"{vendor} {title}.")

    # Product-specific details from actual data — material, type, color
    detail_fragments: list[str] = []
    if type_da:
        detail_fragments.append(type_da)
    if material_info:
        detail_fragments.append(f"i {material_info}")
    if color and color.lower() not in title.lower():
        detail_fragments.append(f"i {color.lower()}")
    if detail_fragments:
        seo_parts.append(" ".join(detail_fragments).capitalize() + ".")

    # Inject the best AI-generated keyword (the most product-specific one)
    # Pick the longest keyword as it tends to be most descriptive
    if seo_keywords:
        best_kw = max(seo_keywords, key=len)
        # Only add if it's not redundant with what we already have
        existing_text = " ".join(seo_parts).lower()
        if best_kw.lower() not in existing_text:
            seo_parts.append(best_kw.capitalize() + ".")

    # Price point
    if retail_price and retail_price > 0:
        seo_parts.append(f"{int(retail_price):,} kr.".replace(",", "."))

    # CTA — only if we have room
    seo_parts.append("Fri fragt over 1.000 kr.")

    seo_desc = " ".join(seo_parts)
    if len(seo_desc) > 320:
        # Drop the CTA first
        seo_desc = " ".join(seo_parts[:-1])
    if len(seo_desc) > 320:
        # Drop price if still too long
        seo_desc = " ".join(seo_parts[:-2])
    if len(seo_desc) > 320:
        seo_desc = seo_desc[:317] + "..."

    # Collect unique sizes for productOptions (preserve order, remove duplicates)
    seen_sizes = set()
    sizes = []
    for v in product.get("variants", []):
        s = v["size"]
        if s and s not in seen_sizes:
            seen_sizes.add(s)
            sizes.append(s)

    if not sizes:
        # Safety fallback: if no sizes exist, create "One Size" variant
        total_qty = sum(v.get("quantity", 0) for v in product.get("variants", []))
        if total_qty == 0:
            total_qty = 1
        product["variants"] = [{"size": "One Size", "quantity": total_qty}]
        sizes = ["One Size"]

    # ── 1. Create product (new ProductCreateInput format) ──
    # Apply [TEST] prefix only to the Shopify title — description/SEO use clean title
    shopify_title = f"[TEST] {title}" if product.get("is_test") else title

    product_input = {
        "title": shopify_title,
        "handle": handle,
        "descriptionHtml": body_html,
        "vendor": vendor,
        "productType": type_da,
        "tags": tags,
        "status": "DRAFT",
        "productOptions": [
            {
                "name": "Size",
                "values": [{"name": s} for s in sizes],
            }
        ],
        "seo": {
            "title": seo_title,
            "description": seo_desc,
        },
    }

    # ── Resolve Shopify standardized product category (applied AFTER creation) ──
    _category_gid = None
    try:
        _shopify_category_name = _map_to_shopify_category(type_da)
        if _shopify_category_name:
            _category_gid = shopify.resolve_category_gid(_shopify_category_name)
            if _category_gid:
                logger.info(f"Resolved category: {type_da} → {_shopify_category_name} ({_category_gid})")
            else:
                logger.warning(f"Could not resolve category GID for: {_shopify_category_name}")
    except Exception as cat_err:
        logger.warning(f"Category resolve failed (non-critical): {cat_err}")

    # Acne Studios uses a custom theme template
    if "acne" in vendor.lower():
        product_input["templateSuffix"] = "acne-products"

    logger.info(f"Creating product '{title}' in Shopify")
    created = shopify.create_product(product_input)
    product_id = created["id"]
    logger.info(f"Product created: {product_id}")

    # ── Set product category via productUpdate (not in ProductInput for API 2024-10) ──
    if _category_gid:
        try:
            shopify.update_product(product_id, {
                "productCategory": {"productTaxonomyNodeId": _category_gid},
            })
            logger.info(f"Category set on {product_id}: {_category_gid}")
        except Exception as cat_err:
            # Non-critical — product exists, category just isn't set
            logger.warning(f"Category update failed (non-critical): {cat_err}")

    # Warnings collector — tracks partial failures (product exists but sub-steps failed)
    warnings = []
    # Error collector for debugging
    errors_log = []

    style_code = product.get("style_code", "")
    country = product.get("country_of_origin", "")
    hs_code = product.get("hs_code", "")
    original_variants = product.get("variants", [])
    size_to_qty = {v["size"]: v.get("quantity", 0) for v in original_variants}
    size_to_ean = {v["size"]: v.get("ean", "") for v in original_variants}
    logger.info(f"[INVENTORY DEBUG] Variants received: {original_variants}")
    logger.info(f"[INVENTORY DEBUG] size_to_qty: {size_to_qty}")
    logger.info(f"[INVENTORY DEBUG] location_id: {location_id}")

    # ── 2. Create ALL variants ──
    # productCreate only creates 1 default variant (first size).
    # We need productVariantsBulkCreate for the remaining sizes.
    variant_edges = created.get("variants", {}).get("edges", [])

    # Find which size was already created as default
    existing_sizes = set()
    for edge in variant_edges:
        node = edge["node"]
        for opt in node.get("selectedOptions", []):
            if opt["name"] == "Size":
                existing_sizes.add(opt["value"])

    # Create missing variants
    missing_variants = []
    for s in sizes:
        if s not in existing_sizes:
            variant_input = {
                "optionValues": [{"optionName": "Size", "name": s}],
                "price": str(retail_price),
            }
            ean = size_to_ean.get(s, "")
            if ean:
                variant_input["barcode"] = ean
            missing_variants.append(variant_input)

    if missing_variants:
        try:
            logger.info(f"Creating {len(missing_variants)} additional variants for '{title}'")
            new_variant_nodes = shopify.create_variants_bulk(product_id, missing_variants)
            for node in new_variant_nodes:
                variant_edges.append({"node": node})
        except Exception as e:
            warnings.append(f"Variants failed: {e}")
            errors_log.append(f"Variant creation: {e}")
            logger.error(f"Variant creation failed for {title}: {e}")

    # ── 3. Update prices on ALL variants (including the default one) ──
    variant_map = {}
    for edge in variant_edges:
        node = edge["node"]
        for opt in node.get("selectedOptions", []):
            if opt["name"] == "Size":
                variant_map[opt["value"]] = node

    variant_updates = []
    for v in original_variants:
        size = v["size"]
        node = variant_map.get(size)
        if not node:
            continue
        variant_updates.append({
            "id": node["id"],
            "price": str(retail_price),
        })

    if variant_updates:
        try:
            shopify.update_variants_bulk(product_id, variant_updates)
        except Exception as e:
            errors_log.append(f"Variant price update: {e}")

    # ── 4. Set SKU, cost, inventory qty per variant ──
    # Optimised 2-pass approach with batch operations:
    #   Pass 1: Enable tracking + set SKU/cost/weight on ALL variants (sequential, with retry)
    #   Pass 2: Batch-set quantities for ALL variants in ONE API call + verify
    logger.info(f"Setting inventory data for {len(variant_edges)} variants of '{title}'")

    # Build lookup: inv_item_id → (var_size, sku, expected_qty)
    variant_inv_map: list[dict] = []
    total_product_qty = sum(size_to_qty.values())
    weight_g = retail_price if retail_price > 0 else 300

    for edge in variant_edges:
        node = edge["node"]
        inv_item_id = node.get("inventoryItem", {}).get("id", "")
        if not inv_item_id:
            continue

        var_size = ""
        for opt in node.get("selectedOptions", []):
            if opt["name"] == "Size":
                var_size = opt["value"]

        sku = f"{style_code}-{var_size}" if style_code else var_size
        qty = size_to_qty.get(var_size, 0)
        if qty > 0:
            set_qty = qty
        elif total_product_qty == 0:
            set_qty = 1
            logger.info(f"Variant '{var_size}': qty=0 but total=0, defaulting to 1")
        else:
            set_qty = qty

        variant_inv_map.append({
            "inv_item_id": inv_item_id,
            "var_size": var_size,
            "sku": sku,
            "set_qty": set_qty,
        })

    logger.info(f"[INVENTORY] {len(variant_inv_map)} variants to configure")

    # ── Brief wait for Shopify to initialise inventory items ──
    if variant_inv_map:
        await asyncio.sleep(0.5)

    # ── Pass 1: Enable tracking + set SKU/cost/weight on ALL variants ──
    failed_tracking: list[dict] = []
    for v in variant_inv_map:
        success = False
        for attempt in range(3):
            try:
                shopify.update_inventory_item(
                    inventory_item_id=v["inv_item_id"],
                    cost=cost_dkk,
                    sku=v["sku"],
                    country_code=country,
                    hs_code=hs_code,
                    tracked=True,
                    weight_grams=weight_g,
                )
                success = True
                break
            except Exception as e:
                if attempt < 2:
                    logger.warning(
                        f"Inventory item update failed for '{v['var_size']}' (attempt {attempt + 1}/3): {e}. Retrying..."
                    )
                    await asyncio.sleep(0.5)
                else:
                    errors_log.append(f"Inventory item ({v['var_size']}): {e}")
                    failed_tracking.append(v)

    if failed_tracking:
        logger.error(f"{len(failed_tracking)} variants failed tracking enablement — inventory will be incomplete")

    # ── Pass 2: Batch-set quantities in ONE API call ──
    if location_id:
        # Brief pause to let tracking propagate
        await asyncio.sleep(0.5)

        # Build batch of (inv_item_id, qty) for all trackable variants
        qty_batch = [
            (v["inv_item_id"], v["set_qty"])
            for v in variant_inv_map
            if v not in failed_tracking
        ]

        if qty_batch:
            for attempt in range(2):
                try:
                    shopify.set_inventory_quantities_batch(qty_batch, location_id)
                    logger.info(f"[INVENTORY] Batch-set quantities for {len(qty_batch)} variants")
                    break
                except Exception as e:
                    if attempt == 0:
                        logger.warning(f"Batch inventory qty failed (attempt 1/2): {e}. Retrying...")
                        await asyncio.sleep(1.0)
                    else:
                        errors_log.append(f"Batch inventory qty: {e}")
                        # Fallback: set individually
                        logger.warning("Falling back to individual inventory quantity setting")
                        for item_id, qty in qty_batch:
                            try:
                                shopify.set_inventory_quantity(item_id, location_id, qty)
                            except Exception as e2:
                                errors_log.append(f"Individual inventory qty fallback: {e2}")

        # ── Verify quantities (batch query) ──
        await asyncio.sleep(0.5)
        inv_item_ids = [v["inv_item_id"] for v in variant_inv_map if v not in failed_tracking]
        try:
            actual_levels = shopify.get_inventory_levels(inv_item_ids, location_id)
        except Exception:
            actual_levels = {}

        # Only retry variants that have wrong quantities
        retry_batch = []
        for v in variant_inv_map:
            if v in failed_tracking:
                continue
            expected = v["set_qty"]
            actual = actual_levels.get(v["inv_item_id"])
            if actual is None or actual != expected:
                logger.warning(
                    f"Inventory mismatch for '{v['var_size']}': expected={expected}, actual={actual}"
                )
                retry_batch.append((v["inv_item_id"], expected))

        if retry_batch:
            logger.info(f"[INVENTORY] Retrying {len(retry_batch)} mismatched variants")
            await asyncio.sleep(0.5)
            try:
                shopify.set_inventory_quantities_batch(retry_batch, location_id)
                logger.info(f"[INVENTORY] Retry batch successful for {len(retry_batch)} variants")
            except Exception as e:
                errors_log.append(f"Inventory retry batch: {e}")

    # ── 5. Set metafields using actual store definitions ──
    gender = product.get("gender", "").lower()
    gender_values = []
    if gender == "unisex":
        gender_values = ["Men", "Women"]
    elif gender in ("men", "menswear", "herrer", "male"):
        gender_values = ["Men"]
    elif gender in ("women", "womenswear", "damer", "female"):
        gender_values = ["Women"]
    else:
        gender_values = ["Women"]

    material = product.get("material", "")
    color = product.get("color", "")
    season = product.get("season", "")

    # Build a lookup from metafield definitions: name -> {namespace, key, type}
    mf_defs = metafield_defs or []
    mf_by_name = {}
    mf_by_key = {}
    for d in mf_defs:
        mf_by_name[d["name"].lower()] = d
        mf_by_key[f"{d['namespace']}.{d['key']}"] = d

    # Map our data to the right metafield definitions
    metafields_to_set = []

    def _add_mf(search_names: list[str], search_keys: list[str], value: str, value_type_override: str = ""):
        """Find matching metafield definition and add to list."""
        found_def = None
        # Try by namespace.key first
        for sk in search_keys:
            if sk in mf_by_key:
                found_def = mf_by_key[sk]
                break
        # Then by display name
        if not found_def:
            for sn in search_names:
                if sn.lower() in mf_by_name:
                    found_def = mf_by_name[sn.lower()]
                    break
        if found_def:
            metafields_to_set.append({
                "namespace": found_def["namespace"],
                "key": found_def["key"],
                "value": value,
                "type": value_type_override or found_def["type"],
            })
        else:
            # Fallback: create with custom namespace
            if search_keys:
                parts = search_keys[0].split(".", 1)
                if len(parts) == 2:
                    metafields_to_set.append({
                        "namespace": parts[0],
                        "key": parts[1],
                        "value": value,
                        "type": value_type_override or "single_line_text_field",
                    })

    # Gender
    _add_mf(
        search_names=["gender", "køn"],
        search_keys=["details.gender", "custom.gender"],
        value=json.dumps(gender_values),
        value_type_override="list.single_line_text_field",
    )

    # Brand collection — COLLECTION REFERENCE, not a text field
    vendor_lower_match = vendor.lower().strip()
    brand_collection_gid = ""

    vendor_variants = [vendor_lower_match]
    vendor_variants.append(vendor_lower_match.replace(" ", "-"))
    vendor_variants.append(vendor_lower_match.replace(" ", ""))
    vendor_clean = re.sub(r"[^a-zæøå0-9\s]", "", vendor_lower_match).strip()
    if vendor_clean and vendor_clean not in vendor_variants:
        vendor_variants.append(vendor_clean)
        vendor_variants.append(vendor_clean.replace(" ", "-"))

    # Two-pass matching: prefer exact match, then fallback to substring match.
    # This prevents "Comme des Garçons" matching "Comme des Garçons Parfums"
    # when a plain "Comme des Garçons" collection exists.
    best_exact_match = ""
    best_substring_match = ""
    best_substring_len = 0  # track specificity for substring matches

    for col in collections:
        col_title = col.get("title", "").lower().strip()
        col_handle = col.get("handle", "").lower().strip()
        for vv in vendor_variants:
            if not vv or len(vv) < 2:
                continue
            # Exact match on handle or title — highest priority
            if vv == col_title or vv == col_handle:
                best_exact_match = col["id"]
                break
            # Word-boundary substring match
            if (
                (vv in col_title and (col_title.startswith(vv) or f" {vv}" in col_title))
                or (vv in col_handle and (col_handle.startswith(vv) or f"-{vv}" in col_handle))
            ):
                # Skip "Parfums/Parfume/Fragrance" collections for non-perfume products
                # (e.g., "Comme des Garçons Parfums" should NOT match for CDG clothing)
                _is_parfum_col = any(
                    kw in col_title for kw in ("parfum", "fragrance", "perfume")
                )
                if _is_parfum_col:
                    continue  # Skip this collection entirely

                # Prefer the shortest matching collection title (most specific to brand)
                if not best_substring_match or len(col_title) < best_substring_len:
                    best_substring_match = col["id"]
                    best_substring_len = len(col_title)
        if best_exact_match:
            break

    brand_collection_gid = best_exact_match or best_substring_match

    if brand_collection_gid:
        _add_mf(
            search_names=["brand collection", "brand_collection", "brand"],
            search_keys=["details.brand_collection", "custom.brand_collection"],
            value=brand_collection_gid,
            value_type_override="collection_reference",
        )

    # Color - Name
    if color:
        _add_mf(
            search_names=["color - name", "color name", "color", "farve"],
            search_keys=["details.color_name", "custom.color_name", "details.color", "custom.color"],
            value=color,
        )

    # Material
    if material:
        _add_mf(
            search_names=["material", "materiale"],
            search_keys=["details.material", "custom.material"],
            value=material,
        )

    # Season
    if season:
        _add_mf(
            search_names=["season", "sæson"],
            search_keys=["details.season", "custom.season"],
            value=season,
        )

    # Set metafields one by one to avoid one failure blocking all
    for mf in metafields_to_set:
        try:
            shopify.set_metafields(product_id, [mf])
        except Exception as e:
            errors_log.append(f"Metafield ({mf['namespace']}.{mf['key']}): {e}")

    # ── 6. Add images (1-5) ──
    logger.info(f"Adding images for '{title}'")
    image_urls = product.get("image_urls", [])
    if not image_urls and product.get("image_url"):
        image_urls = [product["image_url"]]

    if not image_urls:
        warnings.append("Ingen billeder fundet — produktet oprettes uden billeder")
        logger.warning(f"No images available for '{title}' — product will have no images")

    # Pre-validate image URLs with HEAD requests (skip unreachable URLs)
    import requests as _requests
    validated_urls = []
    for img_url in image_urls[:5]:
        try:
            resp = _requests.head(img_url, timeout=5, allow_redirects=True)
            if resp.status_code < 400:
                validated_urls.append(img_url)
            else:
                logger.warning(
                    f"Image URL unreachable (HTTP {resp.status_code}): {img_url[:100]} — skipping"
                )
        except Exception as head_err:
            logger.warning(f"Image URL check failed: {img_url[:100]} — {head_err}")

    if image_urls and not validated_urls:
        warnings.append(f"Alle {len(image_urls)} billed-URLs var ugyldige/udløbet — ingen billeder tilføjet")
        logger.error(f"ALL {len(image_urls)} image URLs failed validation for '{title}'")

    color_name = product.get("color", "")
    alt_base = f"{vendor} {title}"
    alt_parts = [vendor, title]
    if type_da:
        alt_parts.append(type_da)
    if color_name:
        alt_parts.append(color_name)
    alt_seo = " — ".join([alt_base, type_da]) if type_da else alt_base

    images_added = 0
    for idx, img_url in enumerate(validated_urls):
        try:
            if idx == 0:
                alt = alt_seo
            elif idx == 1:
                alt = f"{title} — bagside" if type_da in CLOTHING_TYPES else f"{title} — detalje"
            else:
                alt = f"{title} — billede {idx + 1}"
            shopify.add_image_by_url(product_id, img_url, alt_text=alt)
            images_added += 1
        except Exception as e:
            warnings.append(f"Image {idx + 1} failed: {e}")
            errors_log.append(f"Image {idx + 1}: {e}")
            logger.error(f"Image {idx + 1} failed for {title}: {e}")

    logger.info(f"Images added for '{title}': {images_added}/{len(image_urls)} (validated: {len(validated_urls)})")

    # ── 7. Publish to ALL available channels ──
    logger.info(f"Publishing '{title}' to {len(publications)} channels")
    if publications:
        for pub in publications:
            try:
                shopify.publish_product_single(product_id, pub["id"])
            except Exception as e:
                warnings.append(f"Publish to {pub['name']} failed: {e}")
                errors_log.append(f"Publishing ({pub['name']}): {e}")
                logger.error(f"Publishing to {pub['name']} failed for {title}: {e}")

    # ── 7b. Publish to REQUIRED market catalogs ──
    # These three catalogs MUST always be published to
    REQUIRED_CATALOGS = {"stromstore.com", "stromstore.us", "danmark"}
    if catalogs:
        # Filter to only the required catalogs (case-insensitive match)
        required_cats = [
            cat for cat in catalogs
            if cat["title"].lower().strip() in REQUIRED_CATALOGS
        ]
        # Log which required catalogs were found / missing
        found_names = {cat["title"].lower().strip() for cat in required_cats}
        missing = REQUIRED_CATALOGS - found_names
        if missing:
            miss_str = ", ".join(sorted(missing))
            warnings.append(f"Market catalogs ikke fundet i Shopify: {miss_str}")
            logger.warning(f"Required catalogs missing from Shopify store: {miss_str}")

        logger.info(f"Publishing '{title}' to {len(required_cats)} required market catalogs")
        for cat in required_cats:
            try:
                shopify.publish_product_single(product_id, cat["publication_id"])
                logger.debug(f"Published to catalog '{cat['title']}' ({cat['publication_id']})")
            except Exception as e:
                err_str = str(e)
                # Skip "already published" errors silently
                if "already published" in err_str.lower() or "already exists" in err_str.lower():
                    logger.debug(f"Already published to catalog '{cat['title']}' — skipping")
                else:
                    warnings.append(f"Catalog publish to {cat['title']} failed: {e}")
                    errors_log.append(f"Catalog ({cat['title']}): {e}")
                    logger.error(f"Catalog publish to '{cat['title']}' failed for {title}: {e}")
    else:
        warnings.append("Ingen market catalogs fundet i Shopify — produktet mangler region-tags")
        logger.warning(f"No catalogs fetched for '{title}' — cannot publish to required markets")

    # ── 8. Add to brand collection (skip smart collections — they auto-match) ──
    matched_col = None
    if brand_collection_gid:
        for col in collections:
            if col["id"] == brand_collection_gid:
                matched_col = col
                break

    if matched_col:
        try:
            shopify.add_product_to_collection(matched_col["id"], product_id)
        except Exception as e:
            err_str = str(e)
            if "smart collection" not in err_str.lower() and "Can't manually add" not in err_str:
                errors_log.append(f"Collection ({matched_col['title']}): {e}")

    # ── 9. Create English translation ──
    try:
        translatable = shopify.get_translatable_content(product_id)
        translations = []
        for content in translatable:
            if content["key"] == "title":
                translations.append({
                    "key": "title",
                    "value": title,
                    "digest": content["digest"],
                })
            elif content["key"] == "body_html":
                translations.append({
                    "key": "body_html",
                    "value": build_description_en(product),
                    "digest": content["digest"],
                })
            elif content["key"] == "meta_title":
                # English SEO title: "Brand Product Color | STRØM"
                en_seo_base = f"{vendor} {title}"
                en_color = product.get("color", "")
                if en_color and en_color.lower() not in title.lower():
                    en_seo_base = f"{vendor} {title} {en_color}"
                en_seo_title = f"{en_seo_base} | STRØM"
                if len(en_seo_title) > 70:
                    en_seo_title = f"{vendor} {title} | STRØM"
                if len(en_seo_title) > 70:
                    en_seo_title = f"{title} | STRØM"
                if len(en_seo_title) > 70:
                    en_seo_title = f"{title[:60]} | STRØM"
                translations.append({
                    "key": "meta_title",
                    "value": en_seo_title,
                    "digest": content["digest"],
                })
            elif content["key"] == "meta_description":
                # English SEO description — product-specific details
                en_type = product.get("product_type", "") or type_da
                en_material = product.get("material", "")
                en_desc_parts = [f"{vendor} {title}."]
                en_specifics = []
                if en_type:
                    en_specifics.append(en_type)
                if en_material:
                    # Quick material translation for SEO
                    mat_en = en_material
                    for da_w, en_w in [("bomuld", "cotton"), ("uld", "wool"), ("læder", "leather"),
                                       ("ruskind", "suede"), ("silke", "silk"), ("hør", "linen"),
                                       ("kashmir", "cashmere")]:
                        mat_en = re.sub(rf'\b{da_w}\b', en_w, mat_en, flags=re.IGNORECASE)
                    en_specifics.append(f"in {mat_en}")
                if en_color and en_color.lower() not in title.lower():
                    en_specifics.append(f"in {en_color}")
                if en_specifics:
                    en_desc_parts.append(" ".join(en_specifics).capitalize() + ".")
                if retail_price and retail_price > 0:
                    en_desc_parts.append(f"DKK {int(retail_price):,}.".replace(",", "."))
                en_desc_parts.append("Free shipping over DKK 1,000.")
                en_seo_desc = " ".join(en_desc_parts)
                if len(en_seo_desc) > 320:
                    en_seo_desc = " ".join(en_desc_parts[:-1])
                if len(en_seo_desc) > 320:
                    en_seo_desc = en_seo_desc[:317] + "..."
                translations.append({
                    "key": "meta_description",
                    "value": en_seo_desc,
                    "digest": content["digest"],
                })

        if translations:
            shopify.create_translation(product_id, translations, locale="en")
    except Exception as e:
        errors_log.append(f"Translation: {e}")

    elapsed = round(time.time() - push_start, 2)
    logger.info(f"Push complete for '{title}' in {elapsed}s — {len(warnings)} warnings")

    return {
        "product_id": product_id,
        "title": title,
        "variants": len(variant_edges),
        "errors": errors_log,
        "warnings": warnings,
    }
