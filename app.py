import streamlit as st
import json
import math
import fitz  # pymupdf
import anthropic
import requests
import re
from io import BytesIO
from shopify_api import ShopifyGraphQL

# ─── Page Config ───
st.set_page_config(
    page_title="STRØM — Produkt Import",
    page_icon="⚡",
    layout="wide",
)

# ─── Secrets ───
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")
SHOPIFY_STORE = st.secrets.get("SHOPIFY_STORE", "stroemstore")
SHOPIFY_ACCESS_TOKEN = st.secrets.get("SHOPIFY_ACCESS_TOKEN", "")

# ─── Constants ───
EUR_TO_DKK = 7.46

# Danish product type mapping
TYPE_MAP_DA = {
    "trouser": "Bukser", "pants": "Bukser", "pantalon": "Bukser",
    "trousers": "Bukser", "shorts": "Shorts", "short": "Shorts",
    "bermuda": "Shorts", "shirt": "Skjorter", "chemise": "Skjorter",
    "t-shirt": "T-Shirts", "tee": "T-Shirts", "knit": "Strik",
    "knitwear": "Strik", "pull": "Strik", "pullover": "Strik",
    "sweater": "Strik", "cardigan": "Strik", "jacket": "Jakker",
    "coat": "Jakker", "blazer": "Blazere", "dress": "Kjoler",
    "skirt": "Nederdele", "top": "Toppe", "blouse": "Bluser",
    "hoodie": "Hoodies", "sweatshirt": "Sweatshirts",
    "vest": "Veste", "polo": "Poloer",
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

# Categories that count as "Tøj"
CLOTHING_TYPES = {
    "Bukser", "Shorts", "Skjorter", "T-Shirts", "Strik", "Jakker",
    "Blazere", "Kjoler", "Nederdele", "Toppe", "Bluser", "Hoodies",
    "Sweatshirts", "Veste", "Poloer",
}

SHOE_TYPES = {"Sneakers", "Sandaler", "Støvler", "Loafers", "Sko"}
BAG_TYPES = {"Tasker", "Rygsække", "Punge", "Crossbody tasker"}


# ─── Session State ───
for key, default in {
    "products": [],
    "step": "upload",
    "existing_tags": [],
    "existing_vendors": [],
    "publications": [],
    "collections": [],
    "location_id": None,
    "push_results": [],
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

def extract_pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text


def calculate_retail_price(cost_eur: float, rate: float = EUR_TO_DKK) -> float:
    raw = cost_eur * rate * 2.5
    rounded = round(raw / 50) * 50
    if rounded % 100 == 50 and abs(raw - round(raw / 100) * 100) < 30:
        rounded = round(raw / 100) * 100
    return max(rounded, 50)


def map_type_danish(raw_type: str) -> str:
    key = raw_type.lower().strip()
    return TYPE_MAP_DA.get(key, raw_type.title())


def make_handle(vendor: str, title: str) -> str:
    raw = f"{vendor} {title}"
    handle = raw.lower().strip()
    handle = re.sub(r"[^a-z0-9æøå\s-]", "", handle)
    handle = re.sub(r"[æ]", "ae", handle)
    handle = re.sub(r"[ø]", "oe", handle)
    handle = re.sub(r"[å]", "aa", handle)
    handle = re.sub(r"\s+", "-", handle)
    handle = re.sub(r"-+", "-", handle)
    return handle.strip("-")


def build_tags(product: dict) -> list[str]:
    """Build STRØM tags list based on all rules."""
    tags = []

    # Gender tag
    gender = product.get("gender", "").lower()
    if gender == "unisex":
        tags.extend(["men", "women"])
    elif gender in ("men", "menswear", "herrer"):
        tags.append("men")
    elif gender in ("women", "womenswear", "damer"):
        tags.append("women")

    # Brand tag
    vendor = product.get("vendor", "")
    if vendor:
        tags.append(vendor)

    # Product type tag (Danish)
    type_da = product.get("product_type_da", "")
    if type_da:
        tags.append(type_da)

    # "Tøj" for clothing
    if type_da in CLOTHING_TYPES:
        tags.append("Tøj")

    # Shoe subtypes
    if type_da in SHOE_TYPES:
        tags.append(type_da)

    # Bag subtypes
    if type_da in BAG_TYPES:
        tags.append(type_da)

    # Acne Studios exception
    if "acne" in vendor.lower():
        tags.append("acne-products")

    # Add any AI-suggested tags that match existing store tags
    ai_tags = product.get("ai_tags", [])
    existing = set(st.session_state.existing_tags)
    for t in ai_tags:
        if t in existing and t not in tags:
            tags.append(t)

    return tags


def build_description_da(product: dict) -> str:
    """Build STRØM product description in Danish."""
    title = product.get("title", "")
    vendor = product.get("vendor", "")
    color = product.get("color", "")
    material = product.get("material", "")
    country = product.get("country_of_origin", "")

    country_map = {
        "VN": "Vietnam", "JP": "Japan", "FR": "Frankrig", "PT": "Portugal",
        "IT": "Italien", "CN": "Kina", "TR": "Tyrkiet", "IN": "Indien",
        "DK": "Danmark", "SE": "Sverige", "ES": "Spanien", "DE": "Tyskland",
        "GB": "Storbritannien", "US": "USA", "MA": "Marokko",
        "TN": "Tunesien", "BG": "Bulgarien", "RO": "Rumænien", "PL": "Polen",
    }
    country_name = country_map.get(country.upper(), country) if country else ""

    lines = [f"<p>{title} fra {vendor}.</p>"]
    lines.append("<p>Eksklusivt design med fokus på pasform og kvalitet. Et raffineret stykke designet til en moderne garderobe.</p>")

    details = []
    if color:
        details.append(f"Farve: {color}")
    if material:
        details.append(f"Materiale: {material}")
    if country_name:
        details.append(f"Produceret i: {country_name}")
    if details:
        lines.append(f"<p>{' | '.join(details)}</p>")

    return "\n".join(lines)


def build_description_en(product: dict) -> str:
    """Build English translation of description."""
    title = product.get("title", "")
    vendor = product.get("vendor", "")
    color = product.get("color", "")
    material = product.get("material", "")
    country = product.get("country_of_origin", "")

    country_map = {
        "VN": "Vietnam", "JP": "Japan", "FR": "France", "PT": "Portugal",
        "IT": "Italy", "CN": "China", "TR": "Turkey", "IN": "India",
        "DK": "Denmark", "SE": "Sweden", "ES": "Spain", "DE": "Germany",
        "GB": "United Kingdom", "US": "United States", "MA": "Morocco",
    }
    country_name = country_map.get(country.upper(), country) if country else ""

    lines = [f"<p>{title} from {vendor}.</p>"]
    lines.append("<p>Exclusive design focused on fit and quality. A refined piece designed for a modern wardrobe.</p>")

    details = []
    if color:
        details.append(f"Color: {color}")
    if material:
        details.append(f"Material: {material}")
    if country_name:
        details.append(f"Produced in: {country_name}")
    if details:
        lines.append(f"<p>{' | '.join(details)}</p>")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
# AI EXTRACTION
# ═══════════════════════════════════════════════

def extract_products_with_ai(pdf_text: str, existing_tags: list[str]) -> list[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tag_list = ", ".join(existing_tags) if existing_tags else "(ingen eksisterende tags)"

    system_prompt = f"""Du er en Shopify-produktekspert for STRØM (stromstore.dk), en premium skandinavisk modebutik.

Udtræk produktdata fra leverandørfakturaer og returnér struktureret JSON.

REGLER FOR UDTRÆK:
1. Udtræk ALLE produktlinjer fra fakturaen.
2. Identificér: brand/vendor, produktnavn, style-kode, farve, materiale, oprindelsesland, sæson, størrelser med antal, kostpris (unit net price).

TITEL-REGLER (KRITISK):
- Format: "Produktnavn Farvenavn" (f.eks. "Classic Shirt Blue")
- FJERN alle style-koder, artikelkoder, interne referencer
- FJERN farvekoder (kun farveNAVN)
- Ingen bindestreger, ingen CAPS LOCK, ingen koder
- Rene, læsbare produktnavne

FARVE-REGLER:
- Kun rent farvenavn (ingen koder)
- Oversæt franske farver: BLANC CASSE → Off White, GRIS CHINE → Grey Melange, CREME MOULI → Cream Mouliné, VICHY BLEU → Blue Check, NOIR → Black, BLEU → Blue, ROUGE → Red, VERT → Green, MARRON → Brown, BEIGE → Beige, ROSE → Pink

MATERIALE: Behold som det står (f.eks. "100% NYLON", "100% COTON")

OPRINDELSESLAND: ISO 2-bogstavs kode (VN, JP, FR, PT, IT osv.) eller tom.

SÆSON: "E26" → "SS26", "H26" → "FW26"

PRODUKTTYPE (på engelsk, vi mapper til dansk efterfølgende):
- Pants/Pantalon/Shorts/Bermuda → "Trouser"
- Shirt/Chemise → "Shirt"
- T-shirt/Tee → "T-Shirt"
- Pull/Pullover/Sweater/Knit/Cardigan → "Knit"
- Jacket/Coat/Blazer → "Jacket"
- Dress → "Dress"
- Sneaker/Sandal/Boot → den specifikke skotype
- Bag/Tote/Wallet → den specifikke tasketype

KØN: "Unisex" hvis nævnt. Standard "Womenswear" for American Vintage, "Unisex" for CDG.

EKSISTERENDE TAGS I BUTIKKEN:
{tag_list}

Foreslå relevante tags fra listen ovenfor i "ai_tags" feltet. Du må foreslå nye tags kun hvis ingen eksisterende passer.

Returnér KUN valid JSON array:
[
  {{
    "style_code": "original artikelkode",
    "title": "Rent Produktnavn Farvenavn",
    "vendor": "Brand Name",
    "product_type": "engelsk type (Trouser, Shirt, etc.)",
    "gender": "Womenswear/Menswear/Unisex",
    "color": "rent farvenavn",
    "material": "sammensætning eller tom",
    "country_of_origin": "ISO kode eller tom",
    "hs_code": "HS kode eller tom",
    "season": "SS26/FW26 eller tom",
    "cost_price_eur": enhedspris som tal,
    "ai_tags": ["foreslåede", "tags", "fra", "eksisterende"],
    "variants": [
      {{"size": "S", "quantity": 2}},
      {{"size": "M", "quantity": 3}}
    ]
  }}
]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"Udtræk alle produkter fra denne leverandørfaktura:\n\n{pdf_text}",
            }
        ],
        system=system_prompt,
    )

    response_text = message.content[0].text
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response_text)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_str = response_text.strip()

    return json.loads(json_str)


# ═══════════════════════════════════════════════
# IMAGE SEARCH
# ═══════════════════════════════════════════════

def find_product_image(vendor: str, style_code: str, title: str) -> str:
    """
    Search for product packshot image from brand websites.
    Returns image URL or empty string.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    vendor_lower = vendor.lower()

    # Brand-specific search strategies
    search_urls = []

    if "american vintage" in vendor_lower:
        search_urls.append(
            f"https://www.americanvintage-store.com/en/search?q={requests.utils.quote(style_code)}"
        )
    elif "comme des" in vendor_lower or "cdg" in vendor_lower:
        search_urls.append(
            f"https://shop.doverstreetmarket.com/search?q={requests.utils.quote(style_code)}"
        )
        search_urls.append(
            f"https://www.ssense.com/en-dk/search?q={requests.utils.quote(style_code)}"
        )
    elif "acne" in vendor_lower:
        search_urls.append(
            f"https://www.acnestudios.com/dk/en/search?q={requests.utils.quote(style_code)}"
        )

    # Generic fallbacks
    search_urls.append(
        f"https://www.ssense.com/en-dk/search?q={requests.utils.quote(vendor + ' ' + style_code)}"
    )

    for url in search_urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if not response.ok:
                continue

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")

            # Look for product images
            for img in soup.find_all("img"):
                src = img.get("src", "") or img.get("data-src", "") or ""
                alt = (img.get("alt", "") or "").lower()

                if not src:
                    continue

                # Filter for product images
                is_product = any(kw in src.lower() for kw in [
                    "product", "catalog", "media", "cdn.shopify", "images/products"
                ])
                has_matching_alt = style_code.lower() in alt or any(
                    w in alt for w in title.lower().split()[:2]
                )

                if is_product or has_matching_alt:
                    if src.startswith("//"):
                        src = "https:" + src
                    return src

        except Exception:
            continue

    return ""


# ═══════════════════════════════════════════════
# SHOPIFY PRODUCT PUSH
# ═══════════════════════════════════════════════

def push_product_to_shopify(
    shopify: ShopifyGraphQL,
    product: dict,
    eur_rate: float,
    publications: list[dict],
    collections: list[dict],
    location_id: str,
) -> dict:
    """
    Push a single product to Shopify with all fields.
    Returns result dict with status info.
    """
    title = product["title"]
    vendor = product["vendor"]
    type_da = product.get("product_type_da", "")
    color = product.get("color", "")
    material = product.get("material", "")
    cost_eur = product.get("cost_price_eur", 0)
    cost_dkk = round(cost_eur * eur_rate, 2)
    retail_price = product.get("retail_price_dkk", 0)

    # Build tags
    tags = build_tags(product)

    # Description (Danish)
    body_html = build_description_da(product)

    # Weight = same as price in grams
    weight = retail_price
    weight_unit = "GRAMS"

    # Build variants
    variants = []
    for v in product.get("variants", []):
        variants.append({
            "sku": f"{product.get('style_code', '')}-{v['size']}",
            "price": str(retail_price),
            "weight": weight,
            "weightUnit": weight_unit,
            "inventoryManagement": "SHOPIFY",
            "inventoryPolicy": "DENY",
            "requiresShipping": True,
            "taxable": True,
            "options": [v["size"]],
        })

    # SEO
    seo_title = f"{title} | STRØM"
    seo_desc = f"Køb {title} fra {vendor} hos STRØM. Premium skandinavisk mode."

    # Product input
    product_input = {
        "title": title,
        "bodyHtml": body_html,
        "vendor": vendor,
        "productType": type_da,
        "tags": tags,
        "status": "DRAFT",
        "options": ["Størrelse"],
        "variants": variants,
        "seo": {
            "title": seo_title,
            "description": seo_desc,
        },
    }

    # 1. Create product
    created = shopify.create_product(product_input)
    product_id = created["id"]

    # 2. Set inventory quantities + cost per variant
    variant_edges = created.get("variants", {}).get("edges", [])
    for idx, edge in enumerate(variant_edges):
        variant_node = edge["node"]
        inv_item_id = variant_node["inventoryItem"]["id"]

        # Set quantity
        if idx < len(product.get("variants", [])):
            qty = product["variants"][idx].get("quantity", 0)
            try:
                shopify.set_inventory_quantity(inv_item_id, location_id, qty)
            except Exception:
                pass

        # Set cost + country + HS code
        try:
            shopify.update_inventory_item_cost(
                inv_item_id,
                cost_dkk,
                product.get("country_of_origin", ""),
                product.get("hs_code", ""),
            )
        except Exception:
            pass

    # 3. Set metafields (gender)
    gender = product.get("gender", "").lower()
    gender_values = []
    if gender == "unisex":
        gender_values = ["men", "women"]
    elif gender in ("men", "menswear", "herrer"):
        gender_values = ["men"]
    elif gender in ("women", "womenswear", "damer"):
        gender_values = ["women"]

    if gender_values:
        metafields = [
            {
                "namespace": "custom",
                "key": "gender",
                "value": json.dumps(gender_values) if len(gender_values) > 1 else gender_values[0],
                "type": "list.single_line_text_field" if len(gender_values) > 1 else "single_line_text_field",
            }
        ]

        # Brand collection metafield
        metafields.append({
            "namespace": "custom",
            "key": "brand_collection",
            "value": vendor,
            "type": "single_line_text_field",
        })

        try:
            shopify.set_metafields(product_id, metafields)
        except Exception:
            pass

    # 4. Add image if found
    image_url = product.get("image_url", "")
    if image_url:
        try:
            shopify.add_image_by_url(product_id, image_url, alt_text=title)
        except Exception:
            pass

    # 5. Publish to all channels
    if publications:
        pub_ids = [p["id"] for p in publications]
        try:
            shopify.publish_product(product_id, pub_ids)
        except Exception:
            pass

    # 6. Add to brand collection
    vendor_lower = vendor.lower().strip()
    for col in collections:
        col_title_lower = col["title"].lower().strip()
        col_handle_lower = col["handle"].lower().strip()
        if vendor_lower in col_title_lower or vendor_lower in col_handle_lower:
            try:
                shopify.add_product_to_collection(col["id"], product_id)
            except Exception:
                pass
            break

    # 7. Create English translation
    try:
        translatable = shopify.get_translatable_content(product_id)
        translations = []
        for content in translatable:
            if content["key"] == "title":
                translations.append({
                    "key": "title",
                    "value": title,  # Title stays the same
                    "digest": content["digest"],
                })
            elif content["key"] == "body_html":
                translations.append({
                    "key": "body_html",
                    "value": build_description_en(product),
                    "digest": content["digest"],
                })
            elif content["key"] == "meta_title":
                translations.append({
                    "key": "meta_title",
                    "value": f"{title} | STRØM",
                    "digest": content["digest"],
                })
            elif content["key"] == "meta_description":
                translations.append({
                    "key": "meta_description",
                    "value": f"Shop {title} from {vendor} at STRØM. Premium Scandinavian fashion.",
                    "digest": content["digest"],
                })

        if translations:
            shopify.create_translation(product_id, translations, locale="en")
    except Exception:
        pass

    return {
        "product_id": product_id,
        "title": title,
        "variants": len(variant_edges),
    }


# ═══════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════

st.title("STRØM — Produkt Import")
st.caption("Upload faktura → AI udtræk → Review → Push til Shopify")

# ─── Sidebar ───
with st.sidebar:
    st.header("Status")

    api_ok = bool(ANTHROPIC_API_KEY)
    shopify_ok = bool(SHOPIFY_ACCESS_TOKEN)

    if api_ok:
        st.success("Claude API ✓")
    else:
        st.error("Mangler ANTHROPIC_API_KEY")

    if shopify_ok:
        st.success("Shopify API ✓")
    else:
        st.error("Mangler SHOPIFY_ACCESS_TOKEN")

    # Load Shopify data on first run
    if shopify_ok and not st.session_state.existing_tags:
        shopify = ShopifyGraphQL(SHOPIFY_STORE, SHOPIFY_ACCESS_TOKEN)
        with st.spinner("Henter data fra Shopify..."):
            try:
                st.session_state.existing_tags = shopify.fetch_all_tags()
                st.session_state.existing_vendors = shopify.fetch_all_vendors()
                st.session_state.publications = shopify.fetch_publications()
                st.session_state.collections = shopify.fetch_collections()
                st.session_state.location_id = shopify.get_primary_location_id()
            except Exception as e:
                st.error(f"Shopify-fejl: {e}")

        st.caption(f"{len(st.session_state.existing_tags)} tags")
        st.caption(f"{len(st.session_state.existing_vendors)} vendors")
        st.caption(f"{len(st.session_state.publications)} kanaler")
        st.caption(f"{len(st.session_state.collections)} collections")

    st.divider()

    eur_rate = st.number_input(
        "EUR → DKK", value=EUR_TO_DKK, step=0.01, format="%.2f", key="eur_rate",
    )

    st.divider()

    if st.button("Start forfra"):
        st.session_state.products = []
        st.session_state.step = "upload"
        st.session_state.push_results = []
        st.rerun()


# ═══════════════════════════════════════════════
# STEP 1: Upload
# ═══════════════════════════════════════════════
if st.session_state.step == "upload":
    st.header("1. Upload faktura")

    uploaded_files = st.file_uploader(
        "Træk PDF-fakturaer hertil",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if uploaded_files and st.button("Udtræk produkter", type="primary"):
        all_products = []

        for uploaded_file in uploaded_files:
            with st.spinner(f"Læser {uploaded_file.name}..."):
                pdf_bytes = uploaded_file.read()
                pdf_text = extract_pdf_text(pdf_bytes)

            with st.spinner(f"AI udtræk fra {uploaded_file.name}..."):
                try:
                    products = extract_products_with_ai(
                        pdf_text, st.session_state.existing_tags
                    )
                    for p in products:
                        # Map type to Danish
                        p["product_type_da"] = map_type_danish(p.get("product_type", ""))
                        # Calculate prices
                        p["retail_price_dkk"] = calculate_retail_price(
                            p.get("cost_price_eur", 0), eur_rate
                        )
                        p["cost_dkk"] = round(p.get("cost_price_eur", 0) * eur_rate, 2)
                        # Build tags
                        p["computed_tags"] = build_tags(p)

                    st.success(f"{uploaded_file.name}: {len(products)} produkter")
                    all_products.extend(products)
                except Exception as e:
                    st.error(f"Fejl: {str(e)}")

        if all_products:
            # Search for images
            with st.spinner("Søger produktbilleder..."):
                for p in all_products:
                    img = find_product_image(
                        p["vendor"], p.get("style_code", ""), p["title"]
                    )
                    p["image_url"] = img

            st.session_state.products = all_products
            st.session_state.step = "review"
            st.rerun()


# ═══════════════════════════════════════════════
# STEP 2: Review
# ═══════════════════════════════════════════════
elif st.session_state.step == "review":
    products = st.session_state.products
    st.header(f"2. Review ({len(products)} produkter)")

    for i, p in enumerate(products):
        total_qty = sum(v.get("quantity", 0) for v in p.get("variants", []))

        with st.expander(
            f"**{p['vendor']}** — {p['title']} | {total_qty} stk | {p.get('retail_price_dkk', 0):.0f} DKK",
            expanded=(i == 0),
        ):
            preview_col, data_col = st.columns([1, 1])

            # ── Preview ──
            with preview_col:
                st.markdown("##### Preview")

                # Image
                if p.get("image_url"):
                    st.image(p["image_url"], width=200)
                else:
                    st.caption("Intet billede fundet")

                st.markdown(
                    f"""
                    <div style="border:1px solid #333; border-radius:8px; padding:16px; background:#1a1a1a; margin-top:8px;">
                        <p style="color:#888; font-size:11px; letter-spacing:2px; margin:0;">
                            {p['vendor'].upper()}
                        </p>
                        <h3 style="margin:6px 0 4px 0; color:#fff; font-size:18px;">{p['title']}</h3>
                        <p style="color:#ccc; font-size:13px; margin:0 0 8px 0;">{p.get('color', '')}</p>
                        <p style="font-size:20px; font-weight:600; color:#fff; margin:0 0 12px 0;">
                            {p.get('retail_price_dkk', 0):.0f} DKK
                        </p>
                        <p style="color:#888; font-size:12px; margin:0;">
                            {' · '.join([v['size'] for v in p.get('variants', [])])}
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # ── Data ──
            with data_col:
                st.markdown("##### Shopify-data")
                cost_dkk = round(p.get("cost_price_eur", 0) * eur_rate, 2)

                st.text(f"Handle:    {make_handle(p['vendor'], p['title'])}")
                st.text(f"SKU:       {p.get('style_code', '')}")
                st.text(f"Type:      {p.get('product_type_da', '')}")
                st.text(f"Vendor:    {p['vendor']}")
                st.text(f"Gender:    {p.get('gender', 'N/A')}")
                st.text(f"Cost:      {cost_dkk:.2f} DKK (€{p.get('cost_price_eur', 0):.2f})")
                st.text(f"Retail:    {p.get('retail_price_dkk', 0):.0f} DKK")
                st.text(f"Weight:    {p.get('retail_price_dkk', 0):.0f}g")
                st.text(f"Season:    {p.get('season', 'N/A')}")
                st.text(f"HS Code:   {p.get('hs_code', 'N/A')}")
                st.text(f"Origin:    {p.get('country_of_origin', 'N/A')}")

                sizes = ", ".join([f"{v['size']}({v['quantity']})" for v in p.get("variants", [])])
                st.text(f"Sizes:     {sizes}")

                tags_str = ", ".join(p.get("computed_tags", []))
                st.text(f"Tags:      {tags_str}")

                # Description preview
                st.markdown("**Beskrivelse (DA):**")
                st.markdown(build_description_da(p), unsafe_allow_html=True)

            # ── Editable fields ──
            st.markdown("---")
            ec1, ec2, ec3 = st.columns(3)

            with ec1:
                new_title = st.text_input("Titel", value=p["title"], key=f"title_{i}")
                st.session_state.products[i]["title"] = new_title

            with ec2:
                new_price = st.number_input(
                    "Udsalgspris (DKK)", value=float(p.get("retail_price_dkk", 0)),
                    step=50.0, key=f"price_{i}",
                )
                st.session_state.products[i]["retail_price_dkk"] = new_price

            with ec3:
                new_tags = st.text_input(
                    "Tags", value=tags_str, key=f"tags_{i}",
                )
                st.session_state.products[i]["computed_tags"] = [
                    t.strip() for t in new_tags.split(",") if t.strip()
                ]

    st.divider()

    col1, col2 = st.columns([1, 3])
    with col1:
        can_push = shopify_ok and api_ok
        if st.button("Push til Shopify", type="primary", disabled=not can_push):
            st.session_state.step = "pushing"
            st.rerun()
    with col2:
        if st.button("Tilbage"):
            st.session_state.step = "upload"
            st.session_state.products = []
            st.rerun()


# ═══════════════════════════════════════════════
# STEP 3: Push
# ═══════════════════════════════════════════════
elif st.session_state.step == "pushing":
    st.header("3. Opretter i Shopify...")

    shopify = ShopifyGraphQL(SHOPIFY_STORE, SHOPIFY_ACCESS_TOKEN)
    products = st.session_state.products
    progress = st.progress(0)
    results = []

    for i, p in enumerate(products):
        progress.progress((i + 1) / len(products))

        with st.spinner(f"Opretter {p['title']}..."):
            try:
                result = push_product_to_shopify(
                    shopify=shopify,
                    product=p,
                    eur_rate=eur_rate if "eur_rate" in dir() else EUR_TO_DKK,
                    publications=st.session_state.publications,
                    collections=st.session_state.collections,
                    location_id=st.session_state.location_id or "",
                )
                results.append({"status": "ok", "title": p["title"], "id": result["product_id"]})
                st.success(f"✓ {p['title']}")
            except Exception as e:
                results.append({"status": "error", "title": p["title"], "error": str(e)})
                st.error(f"✗ {p['title']}: {e}")

    progress.progress(1.0)
    st.session_state.push_results = results
    st.session_state.step = "done"
    st.rerun()


# ═══════════════════════════════════════════════
# STEP 4: Done
# ═══════════════════════════════════════════════
elif st.session_state.step == "done":
    st.header("4. Import fuldført")

    results = st.session_state.push_results
    ok = [r for r in results if r["status"] == "ok"]
    fail = [r for r in results if r["status"] == "error"]

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Oprettet", len(ok))
    with col2:
        st.metric("Fejlede", len(fail))

    for r in ok:
        st.success(f"✓ {r['title']}")
    for r in fail:
        st.error(f"✗ {r['title']}: {r['error']}")

    if st.button("Importer flere", type="primary"):
        st.session_state.products = []
        st.session_state.push_results = []
        st.session_state.step = "upload"
        st.rerun()
