import streamlit as st
import json
import csv
import math
import fitz  # pymupdf
import anthropic
import re
from io import BytesIO, StringIO

# ─── Page Config ───
st.set_page_config(
    page_title="STRØM — Produkt Import",
    page_icon="⚡",
    layout="wide",
)

# ─── Secrets / Config ───
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")

# ─── EUR→DKK rate (approximate) ───
EUR_TO_DKK = 7.46

# ─── Session State Init ───
if "products" not in st.session_state:
    st.session_state.products = []
if "step" not in st.session_state:
    st.session_state.step = "upload"
if "csv_data" not in st.session_state:
    st.session_state.csv_data = None


# ─── Helper: Extract text from PDF ───
def extract_pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text


# ─── Helper: STRØM pricing rule ───
def calculate_retail_price(cost_eur: float) -> float:
    """Cost (EUR) × EUR_TO_DKK × 2.5, rounded to nearest 50 or 100 DKK."""
    raw = cost_eur * EUR_TO_DKK * 2.5
    # Round to nearest 50
    rounded = round(raw / 50) * 50
    # If close to a 100, snap to 100
    if rounded % 100 == 50 and abs(raw - round(raw / 100) * 100) < 30:
        rounded = round(raw / 100) * 100
    return max(rounded, 50)  # minimum 50 DKK


# ─── Helper: Generate handle ───
def make_handle(vendor: str, title: str, color: str) -> str:
    """brand-product-color, lowercase, no special chars."""
    raw = f"{vendor} {title} {color}"
    handle = raw.lower().strip()
    handle = re.sub(r"[^a-z0-9\s-]", "", handle)
    handle = re.sub(r"\s+", "-", handle)
    handle = re.sub(r"-+", "-", handle)
    return handle.strip("-")


# ─── Helper: Type mapping ───
TYPE_MAP = {
    "pants": "Trouser",
    "pantalon": "Trouser",
    "trousers": "Trouser",
    "trouser": "Trouser",
    "shorts": "Trouser",
    "short": "Trouser",
    "bermuda": "Trouser",
    "shirt": "Shirt",
    "chemise": "Shirt",
    "t-shirt": "T-Shirt",
    "tee": "T-Shirt",
    "knit": "Knit",
    "knitwear": "Knit",
    "pull": "Knit",
    "pullover": "Knit",
    "sweater": "Knit",
    "cardigan": "Knit",
    "jacket": "Jacket",
    "coat": "Jacket",
    "blazer": "Jacket",
    "dress": "Dress",
    "skirt": "Skirt",
    "top": "Top",
    "blouse": "Top",
}


def map_product_type(raw_type: str) -> str:
    """Map raw product type to STRØM type."""
    key = raw_type.lower().strip()
    return TYPE_MAP.get(key, raw_type.title())


# ─── Helper: AI Extract Products ───
def extract_products_with_ai(pdf_text: str) -> list[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = """You are a Shopify/Matrixify product data expert for STRØM (stromstore.dk), a premium Scandinavian fashion retailer.

Your job: extract product data from supplier invoices and return structured JSON.

EXTRACTION RULES:
1. Extract EVERY product line from the invoice.
2. Identify: brand/vendor, product name, style/article code, color, material, country of origin, season, sizes with quantities, cost price (unit net price).

TITLE RULES (CRITICAL):
- Format: "Product Name Color" (e.g. "Classic Shirt Blue")
- REMOVE all style codes, article codes, internal references from title
- REMOVE color codes (only use color NAME)
- No hyphens, no ALL CAPS, no codes
- Clean, readable product names in the original language or English

COLOR RULES:
- Extract clean color name only (no codes)
- Translate French colors to English: BLANC CASSE → Off White, GRIS CHINE → Grey Melange, CREME MOULI → Cream Mouliné, VICHY BLEU → Blue Check, NOIR → Black, BLEU → Blue, ROUGE → Red, VERT → Green, MARRON → Brown, BEIGE → Beige, ROSE → Pink

MATERIAL: Keep as-is from invoice (e.g. "100% NYLON", "100% COTON")

COUNTRY OF ORIGIN: Extract if available, use ISO 2-letter code (VN, JP, FR, PT, IT, etc.)

SEASON: Extract if available (e.g. "E26" → "SS26", "H26" → "FW26")

PRODUCT TYPE: Classify each product:
- Pants/Pantalon/Trousers/Shorts/Bermuda → "Trouser"
- Shirt/Chemise → "Shirt"
- T-shirt/Tee → "T-Shirt"
- Pull/Pullover/Sweater/Knit/Cardigan → "Knit"
- Jacket/Coat/Blazer → "Jacket"
- Dress → "Dress"

GENDER: Determine from context. If "UNISEX" mentioned, use "Unisex". Otherwise infer from product/collection. Default to "Womenswear" if unclear for American Vintage, "Unisex" for CDG.

SIZE RULES:
- Keep sizes as found (XS, S, M, L, XL, etc.)
- If combined sizes like "M/L", keep as-is

HS CODE: Include if found on invoice (customs/tariff code, usually 8 digits). Otherwise leave empty.

Return ONLY valid JSON array:
[
  {
    "style_code": "original article/style code",
    "title": "Clean Product Name Color",
    "vendor": "Brand Name",
    "product_type": "mapped type",
    "gender": "Womenswear/Menswear/Unisex",
    "color": "clean color name",
    "material": "composition",
    "country_of_origin": "ISO 2-letter code or empty",
    "hs_code": "harmonized system code or empty",
    "season": "SS26/FW26/etc or empty",
    "cost_price_eur": unit net price as number,
    "variants": [
      {"size": "S", "quantity": 2},
      {"size": "M", "quantity": 3}
    ]
  }
]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"Extract all products from this supplier invoice:\n\n{pdf_text}",
            }
        ],
        system=system_prompt,
    )

    response_text = message.content[0].text

    # Extract JSON from response
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response_text)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_str = response_text.strip()

    return json.loads(json_str)


# ─── Helper: Build Matrixify CSV ───
MATRIXIFY_HEADERS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Variant Cost",
    "Variant Country of Origin",
    "Variant Harmonized System Code",
    "Image Src",
    "SEO Title",
    "SEO Description",
    "Status",
]


def build_description(title: str, vendor: str, color: str, material: str, country: str) -> str:
    """Build STRØM-tone product description."""
    country_name = {
        "VN": "Vietnam", "JP": "Japan", "FR": "France", "PT": "Portugal",
        "IT": "Italy", "CN": "China", "TR": "Turkey", "IN": "India",
        "DK": "Denmark", "SE": "Sweden", "ES": "Spain", "DE": "Germany",
        "GB": "United Kingdom", "US": "United States", "MA": "Morocco",
        "TN": "Tunisia", "BG": "Bulgaria", "RO": "Romania", "PL": "Poland",
    }.get(country.upper(), country) if country else ""

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


def build_tags(vendor: str, product_type: str, gender: str, season: str, material: str) -> str:
    """Build STRØM tag string."""
    tags = [vendor, product_type, gender]
    if season:
        tags.append(season)
    if material:
        # Simplify material for tag (e.g. "100% COTON" → "Cotton")
        mat_lower = material.lower()
        if "cotton" in mat_lower or "coton" in mat_lower:
            tags.append("Cotton")
        elif "wool" in mat_lower or "laine" in mat_lower:
            tags.append("Wool")
        elif "nylon" in mat_lower:
            tags.append("Nylon")
        elif "polyester" in mat_lower:
            tags.append("Polyester")
        elif "linen" in mat_lower or "lin" in mat_lower:
            tags.append("Linen")
        elif "silk" in mat_lower or "soie" in mat_lower:
            tags.append("Silk")
        elif "cashmere" in mat_lower or "cachemire" in mat_lower:
            tags.append("Cashmere")
        else:
            tags.append(material.title())
    return ", ".join([t for t in tags if t])


def products_to_matrixify_csv(products: list[dict]) -> str:
    """Convert product list to Matrixify-compatible CSV."""
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(MATRIXIFY_HEADERS)

    for product in products:
        handle = make_handle(
            product["vendor"],
            product.get("clean_title", product["title"]),
            product["color"]
        )
        title = product["title"]
        vendor = product["vendor"]
        product_type = product["product_type"]
        color = product["color"]
        material = product.get("material", "")
        country = product.get("country_of_origin", "")
        hs_code = product.get("hs_code", "")
        season = product.get("season", "")
        gender = product.get("gender", "Womenswear")
        cost_eur = product.get("cost_price_eur", 0)
        retail_price = product.get("retail_price_dkk", calculate_retail_price(cost_eur))

        body_html = build_description(title, vendor, color, material, country)
        tags = build_tags(vendor, product_type, gender, season, material)
        seo_title = f"{title} | STRØM Store"
        seo_desc = f"Shop {title} from {vendor} at STRØM. Premium Scandinavian fashion."

        variants = product.get("variants", [])

        for i, variant in enumerate(variants):
            size = variant["size"]
            qty = variant.get("quantity", 0)
            sku = f"{product.get('style_code', '')}-{size}"

            if i == 0:
                # First row: all product fields + first variant
                row = [
                    handle,             # Handle
                    title,              # Title
                    body_html,          # Body (HTML)
                    vendor,             # Vendor
                    product_type,       # Type
                    tags,               # Tags
                    "FALSE",            # Published (draft)
                    "Size",             # Option1 Name
                    size,               # Option1 Value
                    sku,                # Variant SKU
                    "",                 # Variant Grams
                    "shopify",          # Variant Inventory Tracker
                    qty,                # Variant Inventory Qty
                    "deny",             # Variant Inventory Policy
                    "manual",           # Variant Fulfillment Service
                    retail_price,       # Variant Price
                    "",                 # Variant Compare At Price
                    "TRUE",             # Variant Requires Shipping
                    "TRUE",             # Variant Taxable
                    cost_eur,           # Variant Cost (kept in EUR)
                    country,            # Variant Country of Origin
                    hs_code,            # Variant HS Code
                    "",                 # Image Src
                    seo_title,          # SEO Title
                    seo_desc,           # SEO Description
                    "draft",            # Status
                ]
            else:
                # Subsequent rows: only handle + variant data
                row = [
                    handle,             # Handle
                    "",                 # Title
                    "",                 # Body (HTML)
                    "",                 # Vendor
                    "",                 # Type
                    "",                 # Tags
                    "",                 # Published
                    "Size",             # Option1 Name
                    size,               # Option1 Value
                    sku,                # Variant SKU
                    "",                 # Variant Grams
                    "shopify",          # Variant Inventory Tracker
                    qty,                # Variant Inventory Qty
                    "deny",             # Variant Inventory Policy
                    "manual",           # Variant Fulfillment Service
                    retail_price,       # Variant Price
                    "",                 # Variant Compare At Price
                    "TRUE",             # Variant Requires Shipping
                    "TRUE",             # Variant Taxable
                    cost_eur,           # Variant Cost
                    country,            # Variant Country of Origin
                    hs_code,            # Variant HS Code
                    "",                 # Image Src
                    "",                 # SEO Title
                    "",                 # SEO Description
                    "",                 # Status
                ]

            writer.writerow(row)

    return output.getvalue()


# ─── UI ───

st.title("STRØM — Produkt Import")
st.caption("Upload faktura → AI udtræk → Download Matrixify CSV")

# Sidebar
with st.sidebar:
    st.header("Status")
    if not ANTHROPIC_API_KEY:
        st.error("Mangler ANTHROPIC_API_KEY")
    else:
        st.success("Claude API ✓")

    st.divider()

    st.caption("EUR → DKK kurs")
    eur_rate = st.number_input(
        "Kurs", value=EUR_TO_DKK, step=0.01, format="%.2f", key="eur_rate",
        help="Bruges til prisberegning (cost × kurs × 2.5)"
    )

    st.divider()

    if st.button("Start forfra"):
        st.session_state.products = []
        st.session_state.step = "upload"
        st.session_state.csv_data = None
        st.rerun()


# ════════════════════════════════════════════
# STEP 1: Upload PDF
# ════════════════════════════════════════════
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

            with st.spinner(f"AI udtrækker produktdata fra {uploaded_file.name}..."):
                try:
                    products = extract_products_with_ai(pdf_text)
                    # Post-process: apply STRØM rules
                    for p in products:
                        p["product_type"] = map_product_type(p.get("product_type", ""))
                        p["retail_price_dkk"] = calculate_retail_price(p.get("cost_price_eur", 0))

                    st.success(f"{uploaded_file.name}: {len(products)} produkter fundet")
                    all_products.extend(products)
                except Exception as e:
                    st.error(f"Fejl ved {uploaded_file.name}: {str(e)}")

        if all_products:
            st.session_state.products = all_products
            st.session_state.step = "review"
            st.rerun()


# ════════════════════════════════════════════
# STEP 2: Review & Download
# ════════════════════════════════════════════
elif st.session_state.step == "review":
    products = st.session_state.products
    st.header(f"2. Review ({len(products)} produkter)")

    # Summary table
    for i, p in enumerate(products):
        handle = make_handle(p["vendor"], p["title"], p["color"])
        total_qty = sum(v.get("quantity", 0) for v in p.get("variants", []))
        sizes = ", ".join([f"{v['size']}({v['quantity']})" for v in p.get("variants", [])])

        with st.expander(
            f"**{p['vendor']}** — {p['title']} | {p['color']} | {total_qty} stk | {p['retail_price_dkk']:.0f} DKK",
            expanded=False,
        ):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.text(f"Handle: {handle}")
                st.text(f"SKU: {p.get('style_code', '')}")
                st.text(f"Type: {p['product_type']}")
                st.text(f"Gender: {p.get('gender', 'N/A')}")
            with col2:
                st.text(f"Cost: €{p.get('cost_price_eur', 0):.2f}")
                st.text(f"Retail: {p['retail_price_dkk']:.0f} DKK")
                st.text(f"Material: {p.get('material', 'N/A')}")
                st.text(f"Origin: {p.get('country_of_origin', 'N/A')}")
            with col3:
                st.text(f"Season: {p.get('season', 'N/A')}")
                st.text(f"HS Code: {p.get('hs_code', 'N/A')}")
                st.text(f"Sizes: {sizes}")

            tags = build_tags(p["vendor"], p["product_type"], p.get("gender", ""), p.get("season", ""), p.get("material", ""))
            st.text(f"Tags: {tags}")

            # Editable retail price
            new_price = st.number_input(
                "Ret udsalgspris (DKK)",
                value=float(p["retail_price_dkk"]),
                step=50.0,
                key=f"price_{i}",
            )
            st.session_state.products[i]["retail_price_dkk"] = new_price

    st.divider()

    # Generate CSV
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Generér Matrixify CSV", type="primary"):
            csv_data = products_to_matrixify_csv(st.session_state.products)
            st.session_state.csv_data = csv_data
            st.session_state.step = "download"
            st.rerun()
    with col2:
        if st.button("Tilbage til upload"):
            st.session_state.step = "upload"
            st.session_state.products = []
            st.rerun()


# ════════════════════════════════════════════
# STEP 3: Download
# ════════════════════════════════════════════
elif st.session_state.step == "download":
    st.header("3. CSV klar!")

    csv_data = st.session_state.csv_data
    total_products = len(st.session_state.products)
    total_variants = sum(len(p.get("variants", [])) for p in st.session_state.products)

    st.metric("Produkter", total_products)
    st.metric("Variant-rækker", total_variants)

    st.download_button(
        label="Download Matrixify CSV",
        data=csv_data,
        file_name="strom_import.csv",
        mime="text/csv",
        type="primary",
    )

    st.divider()
    st.subheader("Preview")
    st.code(csv_data[:2000] + ("..." if len(csv_data) > 2000 else ""), language="csv")

    st.divider()
    if st.button("Importer flere fakturaer"):
        st.session_state.products = []
        st.session_state.csv_data = None
        st.session_state.step = "upload"
        st.rerun()
