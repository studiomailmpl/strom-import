import streamlit as st
import json
import math
import base64
import fitz  # pymupdf
import anthropic
import requests
import re
import hashlib
import hmac as hmac_lib
from io import BytesIO
from urllib.parse import urlencode
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
SHOPIFY_CLIENT_ID = st.secrets.get("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = st.secrets.get("SHOPIFY_CLIENT_SECRET", "")

# ─── OAuth Handler ───
# Handles Shopify OAuth install flow to get access token
query_params = st.query_params
if "code" in query_params and "shop" in query_params:
    # Step 2: Exchange authorization code for access token
    shop = query_params["shop"]
    code = query_params["code"]

    if SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET:
        st.title("🔑 Shopify OAuth — Token Exchange")
        token_url = f"https://{shop}/admin/oauth/access_token"
        payload = {
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
            "code": code,
        }
        try:
            resp = requests.post(token_url, json=payload, timeout=15)
            if resp.ok:
                token_data = resp.json()
                access_token = token_data.get("access_token", "")
                st.success("Access token hentet!")
                st.code(access_token, language=None)
                st.warning(
                    "⚠️ Kopiér denne token og indsæt den som SHOPIFY_ACCESS_TOKEN i Streamlit secrets. "
                    "Fjern derefter SHOPIFY_CLIENT_ID og SHOPIFY_CLIENT_SECRET fra secrets — de skal ikke bruges igen."
                )
                st.info("Når du har opdateret secrets, genindlæs appen uden query params.")
            else:
                st.error(f"Token exchange fejlede: {resp.status_code} — {resp.text}")
        except Exception as e:
            st.error(f"Fejl: {e}")
        st.stop()
    else:
        st.error("Mangler SHOPIFY_CLIENT_ID og SHOPIFY_CLIENT_SECRET i secrets for OAuth.")
        st.stop()

elif "shop" in query_params and "code" not in query_params and SHOPIFY_CLIENT_ID:
    # Step 1: Redirect to Shopify OAuth authorization
    shop = query_params["shop"]
    scopes = "write_products,read_products,write_inventory,read_inventory,read_locations,write_publications,read_publications,write_translations,read_translations,read_content"
    redirect_uri = "https://strom-import-lja2bzhdbsjgsbkckvzpvs.streamlit.app"
    nonce = "strom-import-nonce"

    auth_url = (
        f"https://{shop}/admin/oauth/authorize?"
        + urlencode({
            "client_id": SHOPIFY_CLIENT_ID,
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": nonce,
        })
    )
    st.title("🔗 Shopify OAuth")
    st.markdown(f"[Klik her for at autorisere appen →]({auth_url})")
    st.stop()

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

# Default weights in grams by product type (realistic estimates for shipping)
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
    "metafield_defs": [],
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


def extract_pdf_pages_as_images(pdf_bytes: bytes, dpi: int = 200) -> list[str]:
    """Convert each PDF page to a base64-encoded PNG image for Claude Vision.
    Returns list of base64 strings (without data URI prefix).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    zoom = dpi / 72  # 72 is default DPI
    matrix = fitz.Matrix(zoom, zoom)
    for page in doc:
        pix = page.get_pixmap(matrix=matrix)
        img_bytes = pix.tobytes("png")
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        images.append(b64)
    return images


def detect_invoice_currency(pdf_text: str) -> str:
    """Detect the currency of an invoice. Returns 'EUR' or 'DKK'."""
    text_lower = pdf_text.lower()
    # Explicit currency markers
    if "total dkk" in text_lower or "currency: dkk" in text_lower:
        return "DKK"
    if "drawn in: euro" in text_lower or "price(eur)" in text_lower or "amount(eur)" in text_lower:
        return "EUR"
    # Check for DKK symbol or text
    if " dkk" in text_lower and " eur" not in text_lower:
        return "DKK"
    # Default: EUR (most brand invoices are in EUR)
    return "EUR"


def parse_invoice_tables(pdf_bytes: bytes) -> list[dict]:
    """
    Parse product lines from invoice PDF.
    Supports multiple invoice formats:
    - American Vintage: structured tables with Couleur/size rows
    - A.P.C.: product blocks with size grid tables
    - Carhartt WIP / generic: text-based parsing when no tables found

    Returns structured product data with EXACT size→quantity mappings.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"

    # Detect currency
    currency = detect_invoice_currency(full_text)

    # Try table-based extraction first
    products = _parse_tables_structured(doc, currency)

    # If tables didn't work, try text-based parsing
    if not products:
        products = _parse_text_based(full_text, currency)

    return products


def _parse_tables_structured(doc, currency: str) -> list[dict]:
    """Parse invoices that have proper table structures (American Vintage, A.P.C.)."""
    products = []
    used_skus = set()  # Track which SKUs have been matched to a table

    for page in doc:
        try:
            tables = page.find_tables()
        except Exception:
            continue

        page_text = page.get_text()

        # ── Try American Vintage format (header row: "Code article", "Designation") ──
        for table in tables.tables:
            rows = table.extract()
            if not rows or len(rows) < 2:
                continue

            header = rows[0]
            header_text = " ".join([str(c or "") for c in header]).lower()
            if "code article" in header_text or "designation" in header_text:
                products.extend(_parse_american_vintage_table(rows, header, currency))
                continue

            # ── Try A.P.C. format (size grid: "Color", "S", "M", "L", "XL") ──
            if any(str(c or "").strip().lower() == "color" for c in header):
                apc_product = _parse_apc_table(rows, page_text, currency, used_skus)
                if apc_product:
                    used_skus.add(apc_product["style_code"])
                    products.append(apc_product)

    return products


def _parse_american_vintage_table(rows: list, header: list, currency: str) -> list[dict]:
    """Parse American Vintage table format."""
    products = []
    i = 1  # skip header
    while i < len(rows):
        row = rows[i]
        cells = [str(c or "").strip() for c in row]

        if not any(cells):
            i += 1
            continue

        style_code = cells[0] if cells else ""
        if not style_code or len(style_code) < 4 or not re.match(r'^[A-Za-z0-9]', style_code):
            i += 1
            continue

        designation = cells[1] if len(cells) > 1 else ""
        if designation:
            lines = designation.split("\n")
            clean_lines = []
            for line in lines:
                line_strip = line.strip()
                if any(skip in line_strip for skip in ["BL client", "Commande", "Facture", "N°"]):
                    continue
                if line_strip:
                    clean_lines.append(line_strip)
            designation = " ".join(clean_lines).strip()

        if not designation or designation.lower() in ("couleur", ""):
            i += 1
            continue

        cost = 0
        total_qty = 0

        # Find qty and price columns by header
        qty_col = None
        unitnet_col = None
        for hi, h in enumerate(header):
            h_text = str(h or "").strip().lower()
            if h_text in ("qté", "qty", "quantité"):
                qty_col = hi
            if "unit. net" in h_text or "net" in h_text:
                unitnet_col = hi

        if unitnet_col and unitnet_col < len(cells):
            try:
                cost = float(cells[unitnet_col].replace(",", ".").replace(" ", ""))
            except (ValueError, TypeError):
                pass
        if cost == 0:
            for cell in cells[2:]:
                cell_clean = cell.replace(",", ".").replace(" ", "")
                try:
                    val = float(cell_clean)
                    if 10 < val < 500 and cost == 0:
                        cost = val
                except (ValueError, TypeError):
                    pass
        if qty_col and qty_col < len(cells):
            try:
                total_qty = int(cells[qty_col])
            except (ValueError, TypeError):
                pass

        # If invoice is in DKK, convert back to EUR for internal storage
        cost_eur = cost / EUR_TO_DKK if currency == "DKK" else cost

        color = ""
        variants = []
        if i + 2 < len(rows):
            size_row = rows[i + 1]
            qty_row = rows[i + 2]
            size_cells = [str(c or "").strip() for c in size_row]
            qty_cells = [str(c or "").strip() for c in qty_row]

            if size_cells and size_cells[0].lower() == "couleur":
                color = qty_cells[0] if qty_cells else ""
                for col_idx in range(1, len(size_cells)):
                    size_name = size_cells[col_idx]
                    if not size_name or size_name.lower() in ("total", ""):
                        continue
                    qty_str = qty_cells[col_idx] if col_idx < len(qty_cells) else ""
                    if qty_str:
                        try:
                            qty = int(qty_str)
                            if qty > 0:
                                variants.append({"size": size_name, "quantity": qty})
                        except (ValueError, TypeError):
                            pass
                i += 3
            else:
                i += 1
        else:
            i += 1

        if variants:
            products.append({
                "style_code": style_code,
                "designation": designation,
                "color_original": color,
                "cost_price_eur": cost_eur,
                "total_qty": total_qty or sum(v["quantity"] for v in variants),
                "variants": variants,
                "currency_detected": currency,
            })

    return products


def _parse_apc_table(rows: list, page_text: str, currency: str, used_skus: set = None) -> dict | None:
    """Parse A.P.C. size grid table. Returns a single product dict."""
    # A.P.C. tables have:
    #   Row 0: header like ['Color', 'S M L XL\n1 3 3 1 8 48,00 384,00', ...]
    #   Row 1: ['', 'S', 'M', 'L', 'XL', ...]  (size names)
    #   Row 2: ['TIQ - DARK', '1', '3', '3', '1', ...]  (color + quantities)

    if len(rows) < 3:
        return None

    size_row = rows[1]
    qty_row = rows[2]

    size_cells = [str(c or "").strip() for c in size_row]
    qty_cells = [str(c or "").strip() for c in qty_row]

    color = qty_cells[0] if qty_cells else ""
    variants = []

    for col_idx in range(1, len(size_cells)):
        size_name = size_cells[col_idx]
        if not size_name:
            continue
        qty_str = qty_cells[col_idx] if col_idx < len(qty_cells) else ""
        if qty_str:
            try:
                qty = int(qty_str)
                if qty > 0:
                    variants.append({"size": size_name, "quantity": qty})
            except (ValueError, TypeError):
                pass

    if not variants:
        return None

    # Find the SKU and product info from the text ABOVE this table
    # A.P.C. format: "COHBU-M26388 t-shirt standard rue madame GOTS"
    # followed by material, custom code, origin, weight info
    style_code = ""
    designation = ""
    cost = 0
    material = ""
    origin = ""

    # Look for product block pattern: SKU + product name
    # Pattern: COHBU-M26388 t-shirt standard rue madame GOTS
    apc_pattern = re.findall(
        r'([A-Z]{3,6}-[A-Z]\d{4,6})\s+(.+?)(?:\n|MID|GOTS)',
        page_text
    )

    # Match this table to a product block by finding the SKU that appears
    # before the color name in the text
    color_pattern = color.split(" - ")[-1].strip() if " - " in color else color
    text_before_color = page_text.split(color_pattern)[0] if color_pattern in page_text else ""

    if used_skus is None:
        used_skus = set()

    for sku, name in reversed(apc_pattern):  # reversed: closest to the color
        if sku in text_before_color and sku not in used_skus:
            style_code = sku
            designation = name.strip()
            break

    if not style_code:
        # Pick the first unused SKU
        for sku, name in apc_pattern:
            if sku not in used_skus:
                style_code = sku
                designation = name.strip()
                break

    # Extract unit price: "Un. Price(EUR) Amount(EUR)" then "48,00  384,00"
    price_match = re.search(
        rf'{re.escape(style_code)}.*?(\d+[.,]\d{{2}})\s+(\d[\d.,]*\d{{2}})',
        page_text, re.DOTALL
    )
    # Also try: look for total_qty followed by price
    total_qty = sum(v["quantity"] for v in variants)
    price_match2 = re.search(
        rf'{total_qty}\s+(\d+[.,]\d{{2}})\s+(\d[\d.,]*\d{{2}})',
        page_text
    )
    if price_match2:
        try:
            cost = float(price_match2.group(1).replace(",", "."))
        except ValueError:
            pass

    # Extract material
    mat_match = re.search(r'MATERIAL\s*\n?\s*(\d+%\s*\w[\w\s%-]*)', page_text)
    if mat_match:
        material = mat_match.group(1).strip()

    # Extract origin
    origin_match = re.search(r'Origin\s*\n?\s*([A-Za-z]+)', page_text)
    if origin_match:
        origin = origin_match.group(1).strip()

    cost_eur = cost / EUR_TO_DKK if currency == "DKK" else cost

    return {
        "style_code": style_code,
        "designation": designation,
        "color_original": color,
        "cost_price_eur": cost_eur,
        "total_qty": total_qty,
        "variants": variants,
        "material_raw": material,
        "origin": origin,
        "currency_detected": currency,
    }


def _parse_text_based(full_text: str, currency: str) -> list[dict]:
    """
    Fallback: parse invoices from raw text when no tables are found.
    Handles Carhartt WIP and similar text-based invoice formats.

    Carhartt format example:
    25VA051691 I036159 3ONXX S/S Mello Knit Shirt 4 615,00 2.460,00
    Intra.61051000 CO: TR
    Cotton Knit, 12 gauge Mello Stripe, Black ---
    Net: 0,36 - 0,38 kg
    """
    products = []

    # Detect vendor from text
    text_lower = full_text.lower()

    # ── Carhartt WIP format ──
    if "carhartt" in text_lower or "work in progress" in text_lower:
        products = _parse_carhartt_text(full_text, currency)

    # ── Generic text-based fallback ──
    if not products:
        products = _parse_generic_text(full_text, currency)

    return products


def _parse_carhartt_text(full_text: str, currency: str) -> list[dict]:
    """
    Parse Carhartt WIP invoice from text.

    Carhartt PDFs have each field on its own line (not a single row):
      Line N:   25VA051691       (order number)
      Line N+1: I036159          (SKU / article number)
      Line N+2: 3ONXX            (color code)
      Line N+3: S/S Mello Knit Shirt  (product name)
      Line N+4: 4                (quantity)
      Line N+5: 615,00           (unit price)
      Line N+6: 2.460,00         (total price)
    Then supplementary lines:
      Intra.61051000             (HS code)
      CO: TR                     (country of origin)
      Cotton Knit, 12 gauge ...  (material)
      Net: 0,36 - 0,38 kg       (weight)
    """
    products = []
    lines = full_text.split("\n")
    lines = [l.strip() for l in lines]

    # Find product data by locating SKU patterns
    # Carhartt SKU format: letter + 6 digits (e.g. I036159)
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect Carhartt SKU: single letter followed by 6+ digits, alone on a line
        if re.match(r'^[A-Z]\d{5,8}$', line):
            sku = line
            # Read surrounding lines
            # Previous line should be order number, next lines: color, name, qty, price
            order_no = lines[i - 1] if i > 0 else ""
            color_code = lines[i + 1] if i + 1 < len(lines) else ""
            name = lines[i + 2] if i + 2 < len(lines) else ""
            qty_str = lines[i + 3] if i + 3 < len(lines) else ""
            price_str = lines[i + 4] if i + 4 < len(lines) else ""
            total_str = lines[i + 5] if i + 5 < len(lines) else ""

            # Validate: qty should be a number, price should look like a price
            try:
                qty = int(qty_str)
            except (ValueError, TypeError):
                i += 1
                continue

            try:
                unit_price = float(price_str.replace(".", "").replace(",", "."))
            except (ValueError, TypeError):
                i += 1
                continue

            cost_eur = unit_price / EUR_TO_DKK if currency == "DKK" else unit_price

            current_product = {
                "style_code": sku,
                "designation": name,
                "color_original": color_code,
                "cost_price_eur": cost_eur,
                "total_qty": qty,
                "variants": [],  # No size breakdown in Carhartt invoices
                "currency_detected": currency,
                "needs_size_lookup": True,
            }

            # Scan following lines for supplementary info (material, HS code, origin)
            for j in range(i + 6, min(i + 12, len(lines))):
                supp = lines[j]
                if not supp:
                    continue
                # Stop if we hit another order number or "Total"
                if supp.startswith("Total") or re.match(r'^\d{2}[A-Z]{2}\d+$', supp):
                    break
                # HS code: "Intra.61051000"
                hs_match = re.match(r'^Intra\.(\d+)', supp)
                if hs_match:
                    current_product["hs_code"] = hs_match.group(1)
                # Origin: "CO: TR"
                origin_match = re.match(r'^CO:\s*([A-Z]{2})', supp)
                if origin_match:
                    current_product["origin"] = origin_match.group(1)
                # Material: contains cotton/polyester/nylon etc.
                if any(mat in supp.lower() for mat in ["cotton", "polyester", "nylon", "wool", "canvas", "organic"]):
                    current_product["material_raw"] = supp.replace("---", "").strip()

            products.append(current_product)
            i += 6  # Skip past this product block
        else:
            i += 1

    return products


def _parse_generic_text(full_text: str, currency: str) -> list[dict]:
    """Generic fallback text parser for unknown invoice formats."""
    # This will be handled by the AI extraction as a last resort
    return []


def calculate_retail_price(cost_eur: float, rate: float = EUR_TO_DKK) -> float:
    """Calculate retail price: cost × rate × 2.5, rounded to nearest 50 DKK.
    Includes sanity checks for unrealistic prices."""
    if cost_eur <= 0:
        return 0  # Will be flagged in review
    if cost_eur > 5000:
        # Sanity: cost > €5000 is suspicious — might be a parsing error
        pass  # Still calculate but UI will warn
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
    """Sort variants by size in logical order (XS→XXL for letters, ascending for numbers).
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


def build_tags(product: dict) -> list[str]:
    """Build STRØM tags list — selective, no redundant tags."""
    tags = []

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

    # "Tøj" only for clothing categories
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
    existing = set(st.session_state.existing_tags)
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
    seen = set()
    unique_tags = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return unique_tags


def build_description_da(product: dict) -> str:
    """Build STRØM product description in Danish — matches reference product format exactly.
    Format: One paragraph (title + vendor + details) + bullet list (Farve, Materiale).
    """
    title = product.get("title", "")
    vendor = product.get("vendor", "")
    color = product.get("color", "")
    material = product.get("material", "")
    details = product.get("details", "")
    type_da = product.get("product_type_da", "")

    lines = []

    # Paragraph 1: Title + brand + physical details — one flowing paragraph
    detail_text = ""
    if details and len(details.strip()) > 10:
        detail_text = details
    else:
        detail_text = _get_fallback_description(type_da, color)

    # Safety: strip any remaining vendor references from detail_text
    # (the "fra {vendor}" prefix already handles branding)
    if vendor:
        detail_text = re.sub(rf'\s*er fra {re.escape(vendor)}\.?\s*', ' ', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(rf'\s*fra {re.escape(vendor)}\.?\s*', ' ', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(rf"\b{re.escape(vendor)}(?:'s|s)?\b", '', detail_text, flags=re.IGNORECASE)
        # Clean up double spaces and orphaned punctuation
        detail_text = re.sub(r'\s{2,}', ' ', detail_text).strip()
        detail_text = re.sub(r'^\.\s*', '', detail_text)  # Remove leading dot
        # Remove "Materiale: Ikke oplyst" from detail text
        detail_text = re.sub(r'Materiale:\s*[Ii]kke oplyst\.?\s*', '', detail_text).strip()

    lines.append(f"<p>{title} fra {vendor}. {detail_text}</p>")

    # Bullet list: Farve + Materiale + optional Mål for accessories
    bullet_items = []
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


def build_description_en(product: dict) -> str:
    """Build English translation of description — matches reference product format."""
    title = product.get("title", "")
    vendor = product.get("vendor", "")
    color = product.get("color", "")
    material = product.get("material", "")
    details_da = product.get("details", "")
    details_en = product.get("details_en", "")
    type_da = product.get("product_type_da", "")

    # Material translation DA→EN (use word boundary regex to avoid partial matches)
    material_en = material
    if material:
        translations = [
            ("bomuld", "cotton"), ("uld", "wool"), ("silke", "silk"),
            ("hør", "linen"), ("polyamid", "polyamide"), ("viskose", "viscose"),
            ("elastan", "elastane"), ("kashmir", "cashmere"), ("nylon", "nylon"),
            ("polyester", "polyester"),
        ]
        for da, en in translations:
            material_en = re.sub(rf'\b{da}\b', en, material_en, flags=re.IGNORECASE)

    # Use English details if provided by AI, otherwise Danish
    detail_text = details_en or details_da or ""

    # Safety: strip vendor references from detail_text
    if vendor:
        detail_text = re.sub(rf'\s*(?:is )?from {re.escape(vendor)}\.?\s*', ' ', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(rf"\b{re.escape(vendor)}(?:'s|s)?\b", '', detail_text, flags=re.IGNORECASE)
        detail_text = re.sub(r'\s{2,}', ' ', detail_text).strip()
        detail_text = re.sub(r'^\.\s*', '', detail_text)

    lines = []

    # One paragraph: title + vendor + details
    lines.append(f"<p>{title} from {vendor}. {detail_text}</p>")

    # Bullet list: Color + Material
    bullet_items = []
    if color:
        bullet_items.append(f"<li>Color: {color}</li>")
    if material_en:
        bullet_items.append(f"<li>Material: {material_en}</li>")
    if bullet_items:
        lines.append("<ul>" + "".join(bullet_items) + "</ul>")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
# AI EXTRACTION
# ═══════════════════════════════════════════════

def extract_products_with_ai(pdf_text: str, existing_tags: list[str], pdf_images: list[str] = None, table_products: list[dict] = None, active_descriptions: list[dict] = None) -> list[dict]:
    """
    Vision-first extraction:
    - Claude Vision reads the PDF images directly → extracts ALL data (SKU, sizes, qty, prices, material)
    - If deterministic table_products exist, they OVERRIDE AI's sizes/quantities
    - Description style comes from real active product examples
    - Works with ANY invoice format from ANY brand
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tag_list = ", ".join(existing_tags) if existing_tags else "(ingen eksisterende tags)"

    # Build reference descriptions from active products
    description_examples = ""
    if active_descriptions:
        from bs4 import BeautifulSoup as _BS
        good_examples = []
        for ap in active_descriptions:
            html = ap.get("description_html", "")
            if not html or len(html) < 50:
                continue
            text = _BS(html, "html.parser").get_text(separator=" ").strip()
            if len(text) > 40:
                good_examples.append({
                    "title": ap.get("title", ""),
                    "type": ap.get("product_type", ""),
                    "vendor": ap.get("vendor", ""),
                    "description": text[:400],
                })
            if len(good_examples) >= 6:
                break

        if good_examples:
            description_examples = "\n\nEKSEMPLER PÅ BESKRIVELSER FRA AKTIVE PRODUKTER I BUTIKKEN (match denne stil og længde):\n"
            for ex in good_examples:
                description_examples += f'  - "{ex["title"]}" ({ex["vendor"]}, {ex["type"]}): {ex["description"]}\n'
            description_examples += "\nSkriv beskrivelser der matcher dette niveau af detalje og denne tone.\n"

    # Build pre-parsed product summary (if deterministic parser found data)
    table_summary = ""
    if table_products:
        table_summary = "\n\nDETERMINISTISK UDTRUKKET DATA (brug disse størrelser/antal/priser hvis de er tilgængelige):\n"
        for tp in table_products:
            sizes_str = ", ".join([f"{v['size']}({v['quantity']})" for v in tp['variants']]) if tp['variants'] else f"Total: {tp['total_qty']} (ingen størrelses-breakdown)"
            currency_note = f" ({tp.get('currency_detected', 'EUR')}→EUR)" if tp.get('currency_detected') == 'DKK' else ""
            extra = ""
            if tp.get("material_raw"):
                extra += f" | Materiale: {tp['material_raw']}"
            if tp.get("origin"):
                extra += f" | Oprindelse: {tp['origin']}"
            if tp.get("hs_code"):
                extra += f" | HS: {tp['hs_code']}"
            table_summary += f"  - {tp['style_code']} | {tp['designation']} | Farve: {tp['color_original']} | €{tp['cost_price_eur']:.2f}{currency_note} | {sizes_str}{extra}\n"
        table_summary += "\nKRITISK: Brug PRÆCIS disse størrelser, antal og priser. ÆNDR DEM IKKE.\n"

    system_prompt = f"""Du er en Shopify-produktekspert for STRØM (stromstore.dk), en premium skandinavisk modebutik.

DIN OPGAVE: Udtræk ALLE produkter fra denne leverandørfaktura og returnér struktureret JSON.

SE PÅ BILLEDERNE AF FAKTURAEN — de viser det præcise layout med tabeller, størrelser og priser.
Brug teksten som supplement til at bekræfte data.

FOR HVERT PRODUKT SKAL DU UDTRÆKKE:
1. style_code — artikelnummer/SKU fra fakturaen (PRÆCIS som det står)
2. title — produktnavn + original farvenavn i Title Case
3. vendor — brand/leverandør
4. variants — PRÆCISE størrelser og antal fra fakturaens tabel/størrelsesgrid
5. cost_price_eur — enhedspris i EUR (konvertér fra DKK med kurs 7.46 hvis nødvendigt)
6. color — oversat til simpelt dansk/engelsk farvenavn
7. color_original — PRÆCIS farvenavn fra fakturaen
8. material — på dansk (cotton→bomuld, wool→uld osv.)
9. country_of_origin — ISO landekode
10. hs_code — toldkode hvis angivet

VALUTA: Tjek om fakturaen er i EUR eller DKK.
Hvis DKK → divider enhedsprisen med 7.46 for at få EUR.
Tegn: "Total DKK" = DKK, "Price(EUR)" eller "drawn in: Euro" = EUR.

STØRRELSER OG ANTAL — KRITISK:
- Læs PRÆCIST fra fakturaens størrelsesgrid/tabel
- Hver størrelse med sit antal: S(1), M(3), L(3), XL(1)
- "S/S" i et produktnavn betyder "Short Sleeve", IKKE en størrelse
- Hvis fakturaen kun har total antal uden størrelses-breakdown, angiv ALLE størrelser med qty 0 og marker total

TITEL-REGLER:
- Format: "Produktnavn Farvenavn" (f.eks. "S/S Mello Knit Shirt 3Onxx")
- Brug PRÆCISE navne fra fakturaen i Title Case
- "S/S" = Short Sleeve, "L/S" = Long Sleeve (del af produktnavnet, IKKE størrelse)

FARVE ("color"-feltet): Oversæt til simpelt farvenavn:
  NOIR/BLACK → Sort, BLANC/WHITE → Hvid, BLEU → Blå, ROUGE → Rød
  GRIS → Grå, VERT → Grøn, MARRON → Brun, BEIGE → Beige
  DARK/DARK NAVY → Mørk, TIQ/TIQ DARK → Sort, 3ONXX → Sort

PRODUKTTYPE (engelsk): shirt/chemise → "Shirt", pantalon/trouser → "Trouser",
  t-shirt → "T-shirt", hoodie → "Hoodie", knit/pull → "Knit", jacket → "Jacket" osv.

KØN: Bestem fra fakturaen eller brand-kontekst. "Men", "Women" eller "Unisex".

SÆSON: "SS26", "FW26" etc. baseret på fakturadato eller reference.

PRODUKTBESKRIVELSE (dansk "details" + engelsk "details_en"):

KRITISK: "details" og "details_en" må ALDRIG nævne brand/vendor-navnet!
Beskrivelsen starter ALTID med "Denne [type]..." — ALDRIG med produktnavnet eller brandet.
Appen tilføjer selv "[Titel] fra [Vendor]." foran, så du skal KUN skrive den fysiske beskrivelse.

Skriv PRÆCIS som disse eksempler (læg mærke til at de KUN beskriver fysiske egenskaber):

- "Denne taske er fremstillet i ruskinds-læder. Den er designet med to sidelommer i hver side. Tasken har en strop der kan justeres. Indvendigt har tasken et stort rum og en mindre lomme med logo."
- "Disse sneakers er fremstillet i mesh og gummi. De har det klassiske logo på indvendig og udvendig side. Disse sneakers lukkes og strammes med snøre."
- "Denne top er fremstillet i polyester. Den er designet med lav asymmetrisk pasform, med krave og v-hals udskæring. Toppen er detaljeret med blonder forneden."
- "Denne jakke er fremstillet i læder. Den er designet med en afslappet pasform, med krave og sidelommer. Manchetten kan justeres med lynlås."

FORMLEN:
1. "Denne [type] er fremstillet i [materiale]." — brug det danske ord for produkttypen (t-shirt, hoodie, skjorte, bukser osv.)
2. "Den er designet med [pasform/snit/detaljer]." — nævn fit, krave, hals, lukning
3. 1-2 sætninger mere om fysisk konstruktion (lommer, ærmer, søm, kanter, forstærkninger osv.)

REGLER FOR BESKRIVELSE:
- ALDRIG nævn brand/vendor i "details" eller "details_en" — appen tilføjer det automatisk
- ALDRIG nævn størrelse, pris eller tilgængelighed
- ALDRIG brug: "fremgår ikke", "kan ikke udledes", "ikke oplyst", "ikke tilgængelig"
- ALDRIG brug: "perfekt til", "ideel til", "elegant", "raffineret", "tidløs", "æstetik", "udtryk"
- ALDRIG brug: "karakteristiske", "minimalistiske", "moderne udtryk", "klassisk stil"
- KUN beskriv fysiske, konkrete egenskaber man kan se og røre
- Hvis materiale er ukendt: skriv "Denne [type] er designet med..." (spring materiale-sætningen over)
- Dansk beskrivelse: 3-5 sætninger. Engelsk: tilsvarende oversættelse.

GRAMMATIK:
- "hætte" (ALDRIG "hatte") — en hoodie har en hætte
- "fremstillet" (ALDRIG "fremstiller") — passiv form
- "ribkant" / "ribstrik" (ALDRIG "ribkanter")
- Brug korrekt dansk grammatik
{description_examples}

EKSISTERENDE TAGS: {tag_list}

{table_summary}

TAGS ("ai_tags"):
- Giv KUN tags der tilføjer reel værdi for navigation/filtrering
- ALDRIG medtag: farvenavne, materialnavne, engelske produkttyper, køn, vendor-navn
- GODE tags: "Basics", "Nyheder", "acne-products" (kun for Acne Studios)
- DÅRLIGE tags: "Cotton", "Sort", "Shirts", "Male", "T-Shirt", "Minimalist"

MATERIALE ("material"):
- Skriv ALTID på dansk: "100% bomuld", "95% bomuld, 5% elastan"
- Brug: bomuld, uld, silke, hør, polyamid, viskose, elastan, kashmir, polyester, nylon, læder
- Hvis materiale fremgår af fakturaen, brug det. Hvis ikke, lad feltet være tomt "".
- ALDRIG skriv "Ikke oplyst" — brug tom streng i stedet.

Returnér KUN valid JSON array:
[
  {{
    "style_code": "artikelkode",
    "title": "Produktnavn Farve",
    "vendor": "Brand Name",
    "product_type": "Shirt/Trouser/Hoodie/T-shirt/Knit osv.",
    "gender": "Men/Women/Unisex",
    "color": "simpelt dansk farvenavn",
    "color_original": "PRÆCIS farve fra faktura",
    "material": "dansk materiale eller tom streng",
    "details": "Denne [type] er fremstillet i [materiale]. Den er designet med ...",
    "details_en": "This [type] is made from [material]. It is designed with ...",
    "country_of_origin": "ISO kode",
    "hs_code": "toldkode",
    "season": "SS26/FW26",
    "cost_price_eur": 82.44,
    "ai_tags": ["kun", "relevante", "tags"],
    "variants": [{{"size": "S", "quantity": 1}}, {{"size": "M", "quantity": 3}}]
  }}
]"""

    # Build user message with BOTH images and text
    user_content_parts = []

    # Add PDF page images (Vision) — this is the PRIMARY source
    if pdf_images:
        for img_b64 in pdf_images:
            user_content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            })

    # Add text as supplement
    user_content_parts.append({
        "type": "text",
        "text": f"""Udtræk ALLE produkter fra denne leverandørfaktura.
{table_summary}
FAKTURA-TEKST (supplement til billederne):
{pdf_text}""",
    })

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": user_content_parts,
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

    # Try parsing JSON — handle common AI formatting issues
    try:
        raw_products = json.loads(json_str)
    except json.JSONDecodeError as e:
        # Try stripping leading/trailing non-JSON text
        bracket_start = json_str.find("[")
        bracket_end = json_str.rfind("]")
        if bracket_start != -1 and bracket_end != -1:
            json_str = json_str[bracket_start:bracket_end + 1]
            raw_products = json.loads(json_str)
        else:
            raise Exception(f"AI returnerede ugyldig JSON: {str(e)}\n\nResponse (first 500 chars):\n{response_text[:500]}")

    # ── Post-processing ──

    # CRITICAL: Force-override variants with table-extracted data (100% accurate)
    if table_products:
        # Build lookup: style_code → table product
        table_lookup = {tp["style_code"].upper(): tp for tp in table_products}

        for p in raw_products:
            ai_sku = (p.get("style_code") or "").upper()
            if ai_sku in table_lookup:
                tp = table_lookup[ai_sku]
                # Override variants with deterministic table data
                p["variants"] = tp["variants"]
                # Override cost if AI got it wrong
                if tp["cost_price_eur"] > 0:
                    p["cost_price_eur"] = tp["cost_price_eur"]
                # Ensure color_original matches table
                if tp["color_original"] and not p.get("color_original"):
                    p["color_original"] = tp["color_original"]

        # Check if any table products are MISSING from AI output — add them
        ai_skus = {(p.get("style_code") or "").upper() for p in raw_products}
        for tp in table_products:
            if tp["style_code"].upper() not in ai_skus:
                # AI missed this product entirely — create a basic entry
                designation = tp["designation"]
                color_orig = tp["color_original"]
                raw_products.append({
                    "style_code": tp["style_code"],
                    "title": f"{designation.title()} {color_orig.title()}",
                    "vendor": "",  # Will be filled from PDF context
                    "product_type": designation.title(),
                    "gender": "Women",
                    "color": color_orig.title(),
                    "color_original": color_orig,
                    "material": "",
                    "details": "",
                    "details_en": "",
                    "country_of_origin": "",
                    "hs_code": "",
                    "season": "",
                    "cost_price_eur": tp["cost_price_eur"],
                    "ai_tags": [],
                    "variants": tp["variants"],
                })

    for p in raw_products:
        # Fix descriptions: remove forbidden phrases, vendor refs, grammar
        vendor_name = p.get("vendor", "")
        for field in ("details", "details_en"):
            text = p.get(field, "")
            if text:
                text = _clean_description(text, vendor=vendor_name)
                p[field] = text

        # Ensure color_original exists
        if not p.get("color_original"):
            p["color_original"] = p.get("color", "")

        # Title sanity: no ALL CAPS
        title = p.get("title", "")
        if title and title == title.upper() and len(title) > 3:
            p["title"] = title.title()

        # Ensure cost_price_eur is a number
        try:
            p["cost_price_eur"] = float(p.get("cost_price_eur", 0))
        except (ValueError, TypeError):
            p["cost_price_eur"] = 0

        # Clean up variant quantities
        for v in p.get("variants", []):
            try:
                v["quantity"] = int(v.get("quantity", 0))
            except (ValueError, TypeError):
                v["quantity"] = 0

    return raw_products


def _clean_description(text: str, vendor: str = "") -> str:
    """Remove forbidden phrases, fix grammar, strip vendor references from AI-generated descriptions."""
    if not text:
        return text

    # ── Fix common grammar errors ──
    grammar_fixes = [
        (r'\bhatte\b', 'hætte'),        # hoodie har en hætte, ikke hatte
        (r'\bfremstiller\b', 'fremstår'),  # passiv: fremstår, ikke fremstiller
        (r'\bribkanter\b', 'ribkant'),   # singular
    ]
    for pattern, replacement in grammar_fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # ── Remove vendor/brand name references from the description ──
    # (the app prepends "[Title] fra [Vendor]." so it should NOT appear in details)
    if vendor:
        # Remove patterns like "er fra [Vendor].", "er et [Vendor] produkt", etc.
        text = re.sub(rf'\s*er fra {re.escape(vendor)}\.?\s*', ' ', text, flags=re.IGNORECASE)
        text = re.sub(rf'\s*fra {re.escape(vendor)}\.?\s*', ' ', text, flags=re.IGNORECASE)
        # Remove standalone vendor mentions
        text = re.sub(rf"\b{re.escape(vendor)}(?:'s|s)?\b", '', text, flags=re.IGNORECASE)
        # Clean up orphaned "Dette/Denne ... er ." after vendor removal
        text = re.sub(r'(?:Dette|Denne)\s+\w+\s+er\s*\.\s*', '', text)

    # Forbidden phrases — remove sentences containing these
    forbidden = [
        "fremgår ikke", "kan ikke udledes", "ikke tilgængelig",
        "ikke muligt at afgøre", "ikke muligt at fastslå",
        "style den med", "perfekt til", "ideel til", "passer godt til",
        "typisk for mærket", "kendetegnet ved", "kollektionen",
        "fakturaen", "kilden", "manglende information",
        "ikke angivet", "ikke specificeret", "kan ikke bestemmes",
        "ikke oplyst", "ikke kendt",
        "fra fakturaen", "af fakturaen", "på fakturaen",
        "information er ikke", "data er ikke", "oplysninger er ikke",
        # Fluffy marketing language
        "minimalistiske æstetik", "minimalistisk udtryk", "moderne udtryk",
        "karakteristiske", "tidløs", "elegant", "raffineret",
        "minimalistiske", "skandinavisk", "nordisk æstetik",
    ]

    # Split into sentences and filter
    sentences = re.split(r'(?<=[.!?])\s+', text)
    clean_sentences = []
    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(phrase in sentence_lower for phrase in forbidden):
            continue
        # Also skip very short meaningless sentences
        if len(sentence.strip()) < 5:
            continue
        clean_sentences.append(sentence)

    result = " ".join(clean_sentences).strip()

    # If everything was removed, return empty string (fallback will handle it)
    return result


def _get_fallback_description(type_da: str, color: str) -> str:
    """Generate a detailed description based on product type — matches active product style."""
    fallbacks = {
        "Skjorter": "Skjorte med klassisk pasform og knapper foran. Udstyret med spidskrave og lange ærmer med manchetknapper. Buet søm i bunden gør den velegnet til at bære uden på bukserne. Detaljer inkluderer brystlomme og forstærkede sømme.",
        "Bukser": "Bukser med regular fit og mellemhøj talje. Lynlås og knap i livet med bæltestropper. Rette ben med pressefolder giver et rent snit. Sidelommer og baglomme med knap.",
        "T-Shirts": "T-shirt med afslappet pasform og rund ribhals. Korte ærmer med let oprullet kant. Blød bomuldskvalitet med let tekstur. Droppede skuldre giver en moderne silhuet.",
        "Strik": "Striktrøje med rund hals og ribkant ved hals, ærmer og bund. Lange ærmer med regular fit. Mellemvægt strik i blød kvalitet med fin maskestruktur. Velegnet til layering.",
        "Jakker": "Jakke med regular fit og fuld lukning foran. Krave med revers og forede indvendige lommer. Længde til hoften med justérbar detalje i bunden. Forstærkede sømme og kvalitetsknapper.",
        "Blazere": "Blazer med smal pasform og reverskrave. To-knaps lukning foran med indvendige brystlommer. Ærmerne er uforede med surgeon cuffs. Konstrueret med halvforing og single vent i ryggen.",
        "Kjoler": "Kjole med A-linje silhuet og regular fit. Rund hals med let draping over kroppen. Længde til knæet med sideslids. Usynlig lynlås i siden og forstærkede sømme i taljen.",
        "Nederdele": "Nederdel med A-linje snit og mellemhøj talje. Lynlås i siden med hægte-lukning. Længde til knæet med foret inderside. Rene sømme og diskret slids bagpå.",
        "Toppe": "Top med afslappet pasform og rund hals. Let kvalitet med blød finish. Korte ærmer eller ærmeløs med rene kantafslutninger. Lige hem i bunden.",
        "Bluser": "Bluse med feminin pasform og rund hals. Lange ærmer med manchet og knaplukning. Let, luftig kvalitet med diskret mønster eller struktur. Buet søm i bunden.",
        "Hoodies": "Hoodie med oversized fit og træksnor i hætten. Kængurulomme foran med forstærkede sømme. Ribstrik ved ærmer og bund. Blød, børstet inderside for komfort.",
        "Sweatshirts": "Sweatshirt med rund hals og afslappet pasform. Droppede skuldre og lange ærmer med ribkant. Børstet inderside i mellemvægt kvalitet. Ribstrik ved hals, ærmer og bund.",
        "Shorts": "Shorts med regular fit og mellemhøj talje. Lynlås og knap i livet med bæltestropper. Længde til over knæet med sidelommer og baglomme. Ren finish med foldede kanter.",
        "Poloer": "Polo med regular fit og ribstrikket krave. To-knaps lukning foran med korte ærmer. Ribkant ved ærmerne og slids i siderne. Klassisk piké-kvalitet med broderet detalje.",
        "Veste": "Vest uden ærmer med knap- eller lynlåslukning foran. Regular fit med V-hals eller krave. Indvendige lommer og foret front. Ren finish i ryggen.",
        "Sneakers": "Sneakers med snørebånd og polstret tunge. Gummisål med profil for godt greb. Forstærket hælkappe og tåboks. Indvendig polstring og udtagelig indlægssål.",
        "Sandaler": "Sandaler med åben tå og justerbare remme. Fodseng med anatomisk støtte og blød polstring. Ydersål i gummi med godt greb. Detaljer i læder eller tekstil.",
        "Støvler": "Støvler med snørebånd eller lynlås i siden. Forstærket hælkappe og gummisål med profil. Polstret krave og indvendig foring. Ankel- eller mid-calf højde.",
        "Loafers": "Loafers med slip-on design og lav hæl. Klassisk silhuet med dekorativ detalje på overlæderet. Læderforing og polstret indlægssål. Gummisål for holdbarhed.",
        "Sko": "Sko med klassisk silhuet og snørebånd. Overlæder i kvalitetsmateriale med ren finish. Polstret indlægssål og gummisål. Forstærket hælkappe for god støtte.",
        "Tasker": "Taske med justerbar skulderrem og lynlåslukning. Indvendig lomme med lynlås og åbne lommer. Forstærkede sømme og kvalitets-hardware. Spacious hovedrum med organisering.",
        "Rygsække": "Rygsæk med justerbare polstrede stropper og polstret ryg. Hovedrum med lynlås og frontlomme. Indvendig laptoplomme og organiserende lommer. Vandafvisende materiale.",
        "Tørklæder": "Tørklæde i blød kvalitet med fin finish. Let vægt og god draping. Rene kanter med diskret afslutning. Generøs størrelse til flere stylingmuligheder.",
        "Bælter": "Bælte med klassisk spænde i metal. Kvalitetslæder med ren kant-finish. Fem hul-justeringer for optimal pasform. Diskret branding på spændet.",
    }
    desc = fallbacks.get(type_da, f"{type_da} med klassisk pasform.")
    if color:
        desc += f" I farven {color}."
    return desc


# ═══════════════════════════════════════════════
# PRODUCT PAGE SCRAPING (images + details)
# ═══════════════════════════════════════════════

def _scrape_product_details(product_url: str, headers: dict) -> dict:
    """
    Scrape product details (material, description) from a product page.
    Returns dict with 'material', 'description_en', 'description_da' keys.
    """
    from bs4 import BeautifulSoup

    details = {"material": "", "description_en": ""}

    try:
        response = requests.get(product_url, headers=headers, timeout=10)
        if not response.ok:
            return details

        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text(" ", strip=True).lower()

        # ── Extract material/composition ──
        # Look for common patterns: "100% cotton", "70% wool, 30% polyester"
        # Try JSON-LD first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if item.get("@type") == "Product":
                        desc = item.get("description", "")
                        if desc:
                            details["description_en"] = desc[:500]
                        # Check material in additionalProperty
                        for prop in item.get("additionalProperty", []):
                            if "material" in prop.get("name", "").lower() or "composition" in prop.get("name", "").lower():
                                details["material"] = prop.get("value", "")
            except Exception:
                continue

        # Try finding material in page text
        if not details["material"]:
            # Common patterns on fashion sites
            mat_patterns = [
                r'(?:composition|material|matière|fabric)[:\s]*([0-9]+%\s*\w+(?:[,\s]+[0-9]+%\s*\w+)*)',
                r'([0-9]+%\s*(?:cotton|polyester|wool|linen|silk|viscose|elastane|nylon|cashmere|polyamide)(?:[,\s]+[0-9]+%\s*(?:cotton|polyester|wool|linen|silk|viscose|elastane|nylon|cashmere|polyamide))*)',
            ]
            for pat in mat_patterns:
                match = re.search(pat, page_text, re.IGNORECASE)
                if match:
                    details["material"] = match.group(1).strip() if match.lastindex else match.group(0).strip()
                    break

        # Try meta description if no description found
        if not details["description_en"]:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                details["description_en"] = meta_desc["content"][:500]

    except Exception:
        pass

    return details


def _filter_images_with_vision(image_urls: list[str], product_title: str, api_key: str) -> list[str]:
    """
    Use Claude Vision to filter images: keep ONLY product-only images (no models/people).
    Sends small thumbnails to minimize cost. Returns filtered list of URLs.
    """
    if not image_urls or not api_key:
        return image_urls

    # Download images and build vision content
    image_contents = []
    url_map = {}  # index -> url

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    for i, url in enumerate(image_urls):
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if not resp.ok or len(resp.content) < 1000:
                continue

            # Resize to small thumbnail to minimize tokens
            try:
                from PIL import Image
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                img.thumbnail((300, 300))
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=60)
                img_bytes = buf.getvalue()
            except Exception:
                img_bytes = resp.content

            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            image_contents.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": img_b64,
                }
            })
            image_contents.append({
                "type": "text",
                "text": f"Image {i+1}"
            })
            url_map[i+1] = url
        except Exception:
            continue

    if not image_contents or not url_map:
        return image_urls

    # Ask Claude to classify each image
    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt_text = (
            f"Product: {product_title}\n\n"
            f"I have {len(url_map)} images. For each image, tell me if it shows ONLY the product "
            f"(flat lay, packshot, ghost mannequin, product on white/plain background, detail close-up) "
            f"or if it shows a PERSON/MODEL wearing or holding the product.\n\n"
            f"Reply with ONLY a JSON array of the image numbers that show the product WITHOUT any person/model visible. "
            f"Example: [1, 3, 4]\n\n"
            f"If a person's body, face, hands, or legs are visible in the image, do NOT include it."
        )

        image_contents.append({"type": "text", "text": prompt_text})

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": image_contents}],
        )

        response_text = response.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r'\[[\d,\s]*\]', response_text)
        if match:
            approved_indices = json.loads(match.group())
            filtered = [url_map[idx] for idx in approved_indices if idx in url_map]
            if filtered:
                return filtered

    except Exception:
        pass

    # Fallback: return originals if vision fails
    return image_urls


def find_product_images_and_details(vendor: str, style_code: str, title: str, max_images: int = 5) -> dict:
    """
    Search for product images AND scrape product details (material, description) from brand websites.
    Uses SKU to find the actual product page, then extracts everything.

    Returns dict with 'images' (list of URLs) and 'details' (material, description).
    """
    result = {"images": [], "details": {"material": "", "description_en": ""}}

    if not style_code:
        return result

    from bs4 import BeautifulSoup
    from urllib.parse import urlparse, urljoin

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    vendor_lower = vendor.lower()
    sku_lower = style_code.lower().strip()
    sku_encoded = requests.utils.quote(style_code)

    # ── Strategy 1: Brand website — find product page by SKU, then get images + details ──
    brand_search_url = ""
    brand_domain = ""

    if "american vintage" in vendor_lower:
        brand_search_url = f"https://www.americanvintage-store.com/en/search?q={sku_encoded}"
        brand_domain = "americanvintage-store.com"
    # Specific "comme des" sub-brands BEFORE generic CDG catch-all
    elif "comme des" in vendor_lower and "wallet" in vendor_lower:
        brand_search_url = f"https://shop.doverstreetmarket.com/search?q={sku_encoded}"
        brand_domain = "doverstreetmarket.com"
    elif "comme des" in vendor_lower and "parfum" in vendor_lower:
        brand_search_url = f"https://shop.doverstreetmarket.com/search?q={sku_encoded}"
        brand_domain = "doverstreetmarket.com"
    elif "comme des" in vendor_lower or "cdg" in vendor_lower:
        brand_search_url = f"https://shop.doverstreetmarket.com/search?q={sku_encoded}"
        brand_domain = "doverstreetmarket.com"
    elif "acne" in vendor_lower:
        brand_search_url = f"https://www.acnestudios.com/dk/en/search?q={sku_encoded}"
        brand_domain = "acnestudios.com"
    elif "norse projects" in vendor_lower:
        brand_search_url = f"https://www.norseprojects.com/search?q={sku_encoded}"
        brand_domain = "norseprojects.com"
    elif "our legacy" in vendor_lower:
        brand_search_url = f"https://www.ourlegacy.com/search?q={sku_encoded}"
        brand_domain = "ourlegacy.com"
    elif "maison margiela" in vendor_lower or "margiela" in vendor_lower or "mm6" in vendor_lower:
        brand_search_url = f"https://www.maisonmargiela.com/search?q={sku_encoded}"
        brand_domain = "maisonmargiela.com"
    elif "a.p.c" in vendor_lower or "apc" in vendor_lower:
        brand_search_url = f"https://www.apc.fr/en/search?q={sku_encoded}"
        brand_domain = "apc.fr"
    elif "carhartt" in vendor_lower:
        brand_search_url = f"https://www.carhartt-wip.com/en/search?q={sku_encoded}"
        brand_domain = "carhartt-wip.com"
    elif "modstr" in vendor_lower or "modström" in vendor_lower:
        brand_search_url = f"https://www.modstrom.com/search?q={sku_encoded}"
        brand_domain = "modstrom.com"
    elif "sunflower" in vendor_lower:
        brand_search_url = f"https://sunflowershop.dk/search?q={sku_encoded}"
        brand_domain = "sunflowershop.dk"
    elif "salomon" in vendor_lower:
        brand_search_url = f"https://www.salomon.com/en-dk/search?q={sku_encoded}"
        brand_domain = "salomon.com"
    elif "new balance" in vendor_lower:
        brand_search_url = f"https://www.newbalance.dk/search?q={sku_encoded}"
        brand_domain = "newbalance.dk"
    elif "birkenstock" in vendor_lower:
        brand_search_url = f"https://www.birkenstock.com/dk/search?q={sku_encoded}"
        brand_domain = "birkenstock.com"
    elif "service works" in vendor_lower:
        brand_search_url = f"https://serviceworks.co.uk/search?q={sku_encoded}"
        brand_domain = "serviceworks.co.uk"
    elif "alohas" in vendor_lower:
        brand_search_url = f"https://www.alohas.io/search?q={sku_encoded}"
        brand_domain = "alohas.io"
    elif "marni" in vendor_lower:
        brand_search_url = f"https://www.marni.com/search?q={sku_encoded}"
        brand_domain = "marni.com"
    elif "mizuno" in vendor_lower:
        brand_search_url = f"https://www.mizuno.com/search?q={sku_encoded}"
        brand_domain = "mizuno.com"
    elif "timberland" in vendor_lower:
        brand_search_url = f"https://www.timberland.dk/search?q={sku_encoded}"
        brand_domain = "timberland.dk"
    elif "66" in vendor_lower and "north" in vendor_lower:
        brand_search_url = f"https://www.66north.com/search?q={sku_encoded}"
        brand_domain = "66north.com"
    elif "toteme" in vendor_lower:
        brand_search_url = f"https://toteme-studio.com/search?q={sku_encoded}"
        brand_domain = "toteme-studio.com"
    elif "parel" in vendor_lower:
        brand_search_url = f"https://parelstudios.com/search?q={sku_encoded}"
        brand_domain = "parelstudios.com"
    elif "hestra" in vendor_lower:
        brand_search_url = f"https://www.hestragloves.com/search?q={sku_encoded}"
        brand_domain = "hestragloves.com"
    elif "oamc" in vendor_lower:
        brand_search_url = f"https://www.oamc.com/search?q={sku_encoded}"
        brand_domain = "oamc.com"
    elif "sophie bille" in vendor_lower:
        brand_search_url = f"https://sophiebillebrahe.com/search?q={sku_encoded}"
        brand_domain = "sophiebillebrahe.com"
    elif "sofie ladefoged" in vendor_lower:
        brand_search_url = f"https://sofieladefoged.com/search?q={sku_encoded}"
        brand_domain = "sofieladefoged.com"
    elif "dragon diffusion" in vendor_lower:
        brand_search_url = f"https://www.dragondiffusion.com/search?q={sku_encoded}"
        brand_domain = "dragondiffusion.com"
    elif "berner" in vendor_lower:
        brand_search_url = f"https://bernerkuhl.com/search?q={sku_encoded}"
        brand_domain = "bernerkuhl.com"
    elif "gabi" in vendor_lower:
        brand_search_url = f"https://www.gabigamel.com/search?q={sku_encoded}"
        brand_domain = "gabigamel.com"
    elif "fichi" in vendor_lower:
        brand_search_url = f"https://fichi.dk/search?q={sku_encoded}"
        brand_domain = "fichi.dk"
    elif "flowerism" in vendor_lower:
        brand_search_url = f"https://flowerismstudio.com/search?q={sku_encoded}"
        brand_domain = "flowerismstudio.com"
    elif "flatlist" in vendor_lower:
        brand_search_url = f"https://flatlisteyewear.com/search?q={sku_encoded}"
        brand_domain = "flatlisteyewear.com"
    elif "monokel" in vendor_lower:
        brand_search_url = f"https://monokeleyewear.com/search?q={sku_encoded}"
        brand_domain = "monokeleyewear.com"

    # Step 1: Try brand site — find product page, get images AND details
    if brand_search_url:
        product_page_url = _find_product_page_from_search(brand_search_url, sku_lower, brand_domain, headers)
        if product_page_url:
            imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
            if imgs:
                result["images"] = imgs
            # Also scrape product details (material, description)
            result["details"] = _scrape_product_details(product_page_url, headers)
            if result["images"]:
                return result

    # ── Strategy 2: Multi-brand retailers — expanded list ──
    retailer_searches = [
        (f"https://www.ssense.com/en-dk/search?q={sku_encoded}", "ssense.com"),
        (f"https://www.farfetch.com/dk/shopping/search/items/?q={sku_encoded}", "farfetch.com"),
        (f"https://www.endclothing.com/dk/catalogsearch/result/?q={sku_encoded}", "endclothing.com"),
        (f"https://www.mrporter.com/en-dk/search?q={sku_encoded}", "mrporter.com"),
        (f"https://www.matchesfashion.com/search?q={sku_encoded}", "matchesfashion.com"),
        (f"https://www.mytheresa.com/search?q={sku_encoded}", "mytheresa.com"),
        (f"https://www.luisaviaroma.com/en-dk/search?q={sku_encoded}", "luisaviaroma.com"),
    ]

    for search_url, domain in retailer_searches:
        product_page_url = _find_product_page_from_search(search_url, sku_lower, domain, headers)
        if product_page_url:
            imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
            if imgs:
                result["images"] = imgs
                # Scrape details if we didn't get them from brand site
                if not result["details"]["material"]:
                    result["details"] = _scrape_product_details(product_page_url, headers)
                return result

    # ── Strategy 3: Try alternative SKU formats ──
    # Some brands use partial SKUs or different separators
    alt_skus = set()
    # Remove color suffix: "COHBU-M26388" → try just "M26388"
    if "-" in style_code:
        parts = style_code.split("-")
        for part in parts:
            if len(part) >= 5:
                alt_skus.add(part)
    # Remove dots/spaces: "A.P.C." style
    alt_skus.add(style_code.replace("-", ""))
    alt_skus.add(style_code.replace(".", ""))
    alt_skus.discard(style_code)  # Don't retry the same SKU

    for alt_sku in alt_skus:
        alt_encoded = requests.utils.quote(alt_sku)
        # Try brand site with alternate SKU
        if brand_search_url and brand_domain:
            alt_brand_url = f"https://{brand_domain}/search?q={alt_encoded}" if "www." not in brand_domain else f"https://www.{brand_domain}/search?q={alt_encoded}"
            product_page_url = _find_product_page_from_search(alt_brand_url, alt_sku.lower(), brand_domain, headers)
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, alt_sku.lower(), headers, max_images)
                if imgs:
                    result["images"] = imgs
                    result["details"] = _scrape_product_details(product_page_url, headers)
                    return result
        # Try top retailers with alternate SKU
        for search_url_tmpl, domain in [
            (f"https://www.ssense.com/en-dk/search?q={alt_encoded}", "ssense.com"),
            (f"https://www.endclothing.com/dk/catalogsearch/result/?q={alt_encoded}", "endclothing.com"),
        ]:
            product_page_url = _find_product_page_from_search(search_url_tmpl, alt_sku.lower(), domain, headers)
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, alt_sku.lower(), headers, max_images)
                if imgs:
                    result["images"] = imgs
                    if not result["details"]["material"]:
                        result["details"] = _scrape_product_details(product_page_url, headers)
                    return result

    # ── Strategy 4: Search by product title + vendor (last resort) ──
    title_search = requests.utils.quote(f"{vendor} {title}")
    title_retailers = [
        (f"https://www.ssense.com/en-dk/search?q={title_search}", "ssense.com"),
        (f"https://www.endclothing.com/dk/catalogsearch/result/?q={title_search}", "endclothing.com"),
    ]
    for search_url, domain in title_retailers:
        product_page_url = _find_product_page_from_search(search_url, sku_lower, domain, headers)
        if product_page_url:
            imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
            if imgs:
                result["images"] = imgs
                if not result["details"]["material"]:
                    result["details"] = _scrape_product_details(product_page_url, headers)
                return result

    # ── Strategy 5: Direct brand CDN patterns ──
    if "american vintage" in vendor_lower:
        found = []
        for suffix in ["_1", "-1", "_front", ""]:
            test_url = f"https://www.americanvintage-store.com/media/catalog/product/{sku_lower}{suffix}.jpg"
            try:
                resp = requests.head(test_url, headers=headers, timeout=5, allow_redirects=True)
                if resp.ok and "image" in resp.headers.get("content-type", ""):
                    found.append(test_url)
                    if len(found) >= max_images:
                        break
            except Exception:
                continue
        if found:
            result["images"] = found
            return result

    return result


# Backward-compatible wrapper
def find_product_images(vendor: str, style_code: str, title: str, max_images: int = 5) -> list[str]:
    """Legacy wrapper — returns just image URLs."""
    result = find_product_images_and_details(vendor, style_code, title, max_images)
    return result.get("images", [])


def _find_product_page_from_search(search_url: str, sku_lower: str, domain: str, headers: dict) -> str:
    """
    From a search results page, find a link to the actual product page.
    Matches by checking if the SKU appears in the product page URL or link text.
    Returns the product page URL or empty string.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        if not response.ok:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        # Look for product links that contain the SKU in the href or link text
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            href_lower = href.lower()
            link_text = link.get_text(strip=True).lower()

            # The SKU (or a significant part of it) should appear in the product URL
            # Many sites encode the style code in the URL path
            sku_parts = re.split(r"[-_/\s]", sku_lower)
            # Use the main part of the SKU (first chunk, usually the model code)
            main_sku = sku_parts[0] if sku_parts else sku_lower

            if len(main_sku) < 3:
                main_sku = sku_lower

            # Check if this looks like a product page link (not a category, filter, etc.)
            is_product_link = any(seg in href_lower for seg in [
                "/product/", "/products/", "/item/", "/p/",
                "/shopping/", "/shop/", "/en/", "/dk/",
            ])

            has_sku_in_url = main_sku in href_lower or sku_lower.replace("-", "") in href_lower.replace("-", "")
            has_sku_in_text = main_sku in link_text or sku_lower in link_text

            if has_sku_in_url or (is_product_link and has_sku_in_text):
                # Build absolute URL
                full_url = urljoin(search_url, href)
                # Verify it's on the same domain
                if domain in full_url:
                    return full_url

        # Fallback: check JSON-LD structured data for product URLs
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json as _json
                ld = _json.loads(script.string or "")
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if item.get("@type") in ("Product", "ItemPage"):
                        url = item.get("url", "")
                        if url and (sku_lower in url.lower()):
                            return url
                    # Check itemListElement for search results
                    for el in item.get("itemListElement", []):
                        url = el.get("url", "")
                        if url and (sku_lower in url.lower()):
                            return url
                        item_data = el.get("item", {})
                        if isinstance(item_data, dict):
                            url = item_data.get("url", "")
                            if url and (sku_lower in url.lower()):
                                return url
            except Exception:
                continue

    except Exception:
        pass

    return ""


def _get_all_images_from_product_page(product_url: str, sku_lower: str, headers: dict, max_images: int = 5) -> list[str]:
    """
    From a specific product page, extract images for THIS specific product only.

    Strategy:
    1. JSON-LD Product.image — most reliable, curated by the site
    2. OG image — usually the main product photo
    3. img tags — ONLY if SKU is in the URL or alt text (strict matching)

    Never collect generic "product-looking" images — that pulls in related products.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    # Separate buckets: trusted (JSON-LD/OG) vs SKU-matched img tags
    trusted_imgs = []   # JSON-LD and OG — curated by the page
    sku_imgs = []       # img tags where SKU appears in src or alt
    seen_keys = set()

    def _dedup_key(url: str) -> str:
        """Generate aggressive dedup key — strips ALL query params, size suffixes, CDN transforms."""
        from urllib.parse import urlparse
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlparse(url)
        path = parsed.path.lower().strip("/")
        path = re.sub(r'_\d+x\d*(?=\.\w+$)', '', path)
        path = re.sub(r'_(grande|large|medium|small|compact|master|pico|icon|thumb)(?=\.\w+$)', '', path)
        path = re.sub(r'/[whcq]_\d+', '', path)
        path = re.sub(r'\.(jpg|jpeg|png|webp|avif)$', '', path)
        return f"{parsed.netloc}/{path}"

    def _add(url: str, bucket: list):
        if not url or len(url) < 15:
            return
        if url.startswith("//"):
            url = "https:" + url
        key = _dedup_key(url)
        if key in seen_keys:
            return
        if not _is_valid_product_image(url):
            return
        seen_keys.add(key)
        bucket.append(url)

    try:
        response = requests.get(product_url, headers=headers, timeout=10)
        if not response.ok:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # ── 1. JSON-LD structured data — most reliable source ──
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json as _json
                ld = _json.loads(script.string or "")
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if item.get("@type") == "Product":
                        images = item.get("image", [])
                        if isinstance(images, str):
                            images = [images]
                        elif isinstance(images, dict):
                            images = [images.get("url", "")]
                        for img_url in images:
                            if img_url:
                                _add(img_url, trusted_imgs)
            except Exception:
                continue

        # ── 2. OG image — usually the hero product photo ──
        for og in soup.find_all("meta", property="og:image"):
            if og.get("content"):
                _add(og["content"], trusted_imgs)

        # ── 3. img tags — STRICT: only if SKU is in URL or alt text ──
        sku_normalized = sku_lower.replace("-", "").replace("_", "").replace(" ", "")
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "") or img.get("data-zoom-image", "") or ""
            alt = (img.get("alt", "") or "").lower()

            if not src or len(src) < 10:
                continue

            width = img.get("width", "")
            if width and width.isdigit() and int(width) < 150:
                continue

            src_lower = src.lower()
            src_normalized = src_lower.replace("-", "").replace("_", "")

            # STRICT check: SKU must appear in URL or alt text
            has_sku = (sku_lower in src_lower
                       or sku_normalized in src_normalized
                       or sku_lower in alt)

            if has_sku:
                full_src = src
                if src.startswith("/"):
                    full_src = urljoin(product_url, src)
                _add(full_src, sku_imgs)

    except Exception:
        pass

    # Merge: trusted images first, then SKU-matched img tags
    all_candidates = trusted_imgs + sku_imgs
    if not all_candidates:
        return []

    # Score images: prefer packshots over model shots
    scored = []
    for url in all_candidates:
        url_lower = url.lower()
        score = 0

        packshot_keywords = ["flat", "packshot", "still", "ghost", "product",
                             "detail", "close", "cut-out", "cutout", "_e", "_e_",
                             "pack", "lay", "front", "back"]
        for kw in packshot_keywords:
            if kw in url_lower:
                score += 10

        model_keywords = ["model", "look", "worn", "outfit", "lifestyle",
                          "campaign", "editorial", "runway", "wearing",
                          "_m_", "_m.", "mannequin", "styled"]
        for kw in model_keywords:
            if kw in url_lower:
                score -= 20

        if sku_lower in url_lower:
            score += 5

        scored.append((score, url))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [url for _, url in scored]

    # Content-based deduplication: download, resize to tiny thumbnail, hash
    unique_urls = []
    seen_hashes = set()

    for url in candidates:
        if len(unique_urls) >= max_images:
            break
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if not resp.ok or len(resp.content) < 1000:
                continue

            try:
                from PIL import Image
                img = Image.open(BytesIO(resp.content)).convert("RGB").resize((16, 16))
                pixel_data = img.tobytes()
                content_hash = hashlib.md5(pixel_data).hexdigest()
            except Exception:
                content_hash = hashlib.md5(resp.content).hexdigest()

            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            unique_urls.append(url)
        except Exception:
            continue

    return unique_urls


def _is_valid_product_image(url: str) -> bool:
    """Check if URL looks like a valid product image (not a logo, icon, or placeholder)."""
    if not url or len(url) < 15:
        return False
    url_lower = url.lower()
    skip_patterns = ["logo", "icon", "favicon", "placeholder", "spacer", "pixel", "tracking",
                     "badge", "banner", "sprite", "social", "payment", "flag", "arrow",
                     "swatch", "color-chip", "thumbnail", "1x1", "blank"]
    for pattern in skip_patterns:
        if pattern in url_lower:
            return False
    is_image = any(url_lower.endswith(ext) or f"{ext}?" in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp", ".avif"])
    is_cdn = any(cdn in url_lower for cdn in ["cdn.", "imgix", "cloudfront", "cloudinary", "shopify", "media/", "images/"])
    return is_image or is_cdn


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
    metafield_defs: list[dict] = None,
) -> dict:
    """
    Push a single product to Shopify using the new GraphQL API format.
    Flow: create product → update variants → inventory → metafields → images → publish → collections → translations
    """
    title = product["title"]
    vendor = product["vendor"]
    type_da = product.get("product_type_da", "")
    cost_eur = product.get("cost_price_eur", 0)
    cost_dkk = round(cost_eur * eur_rate, 2)
    retail_price = product.get("retail_price_dkk", 0)

    # Build tags
    tags = build_tags(product)

    # Description (Danish)
    body_html = build_description_da(product)

    # SEO (Shopify: max 70 chars title, max 320 chars description)
    seo_title = f"{title} | {vendor} | STRØM"
    if len(seo_title) > 70:
        seo_title = f"{title} | STRØM"
    if len(seo_title) > 70:
        seo_title = f"{title[:60]} | STRØM"

    # Build richer meta description with type, material, color
    color_info = product.get("color", "")
    material_info = product.get("material", "")
    seo_parts = [f"Køb {title} fra {vendor} hos STRØM."]
    if type_da and material_info:
        seo_parts.append(f"{type_da} i {material_info}.")
    elif type_da:
        seo_parts.append(f"{type_da} fra {vendor}.")
    seo_parts.append("Fri fragt over 1.000 kr. Hurtig levering.")
    seo_desc = " ".join(seo_parts)
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
        raise Exception(f"Ingen størrelser fundet for {title}")

    # ── 1. Create product (new ProductCreateInput format) ──
    product_input = {
        "title": title,
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

    # Acne Studios uses a custom theme template
    if "acne" in vendor.lower():
        product_input["templateSuffix"] = "acne-products"

    created = shopify.create_product(product_input)
    product_id = created["id"]

    # Error collector for debugging
    errors_log = []

    style_code = product.get("style_code", "")
    country = product.get("country_of_origin", "")
    hs_code = product.get("hs_code", "")
    original_variants = product.get("variants", [])
    size_to_qty = {v["size"]: v.get("quantity", 0) for v in original_variants}

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
            missing_variants.append({
                "optionValues": [{"optionName": "Size", "name": s}],
                "price": str(retail_price),
            })

    if missing_variants:
        try:
            new_variant_nodes = shopify.create_variants_bulk(product_id, missing_variants)
            # Add new variants to variant_edges for downstream processing
            for node in new_variant_nodes:
                variant_edges.append({"node": node})
        except Exception as e:
            errors_log.append(f"Variant creation: {e}")

    # ── 3. Update prices on ALL variants (including the default one) ──
    # Rebuild variant map after creating new variants
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
    for edge in variant_edges:
        node = edge["node"]
        inv_item_id = node.get("inventoryItem", {}).get("id", "")
        if not inv_item_id:
            continue

        # Find matching size
        var_size = ""
        for opt in node.get("selectedOptions", []):
            if opt["name"] == "Size":
                var_size = opt["value"]

        # Build SKU: style_code-size
        sku = f"{style_code}-{var_size}" if style_code else var_size

        # Update inventory item: SKU, cost, country, HS code, weight
        # Weight = retail price in grams (e.g. 800 DKK → 800g)
        weight_g = retail_price if retail_price > 0 else 300
        try:
            shopify.update_inventory_item(
                inventory_item_id=inv_item_id,
                cost=cost_dkk,
                sku=sku,
                country_code=country,
                hs_code=hs_code,
                tracked=True,
                weight_grams=weight_g,
            )
        except Exception as e:
            errors_log.append(f"Inventory item ({var_size}): {e}")

        # Set quantity — always set if location_id exists (even qty=0 ensures "stocked")
        qty = size_to_qty.get(var_size, 0)
        if location_id:
            # If total product qty > 0 but this specific size has 0, still set it
            # to ensure the item is "stocked at location"
            total_product_qty = sum(size_to_qty.values())
            set_qty = qty if qty > 0 else (1 if total_product_qty == 0 and len(size_to_qty) <= 1 else qty)
            try:
                shopify.set_inventory_quantity(inv_item_id, location_id, set_qty)
            except Exception as e:
                # Retry once on failure
                try:
                    import time
                    time.sleep(1)
                    shopify.set_inventory_quantity(inv_item_id, location_id, set_qty)
                except Exception as e2:
                    errors_log.append(f"Inventory qty ({var_size}): {e2}")

    # ── 4. Set metafields using actual store definitions ──
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

    # Build a lookup from metafield definitions: name → {namespace, key, type}
    mf_defs = metafield_defs or []
    mf_by_name = {}
    mf_by_key = {}
    for d in mf_defs:
        mf_by_name[d["name"].lower()] = d
        mf_by_key[f"{d['namespace']}.{d['key']}"] = d

    # Map our data to the right metafield definitions
    # We try multiple matching strategies: by key, by name
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

    # Brand collection — this is a COLLECTION REFERENCE, not a text field
    # Find the matching collection GID for the vendor (fuzzy matching)
    vendor_lower_match = vendor.lower().strip()
    brand_collection_gid = ""

    # Build normalized vendor variants for matching
    vendor_variants = [vendor_lower_match]
    # Handle multi-word brands: "American Vintage" → also try "american-vintage"
    vendor_variants.append(vendor_lower_match.replace(" ", "-"))
    vendor_variants.append(vendor_lower_match.replace(" ", ""))
    # Handle special chars: "A.P.C." → "apc"
    vendor_clean = re.sub(r"[^a-zæøå0-9\s]", "", vendor_lower_match).strip()
    if vendor_clean and vendor_clean not in vendor_variants:
        vendor_variants.append(vendor_clean)
        vendor_variants.append(vendor_clean.replace(" ", "-"))

    for col in collections:
        col_title = col.get("title", "").lower().strip()
        col_handle = col.get("handle", "").lower().strip()
        for vv in vendor_variants:
            if not vv:
                continue
            if vv in col_title or vv in col_handle or col_title in vv or col_handle == vv:
                brand_collection_gid = col["id"]  # e.g. "gid://shopify/Collection/123456"
                break
        if brand_collection_gid:
            break

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

    # ── 5. Add images (1-5) ──
    image_urls = product.get("image_urls", [])
    # Fallback to single image_url for backward compat
    if not image_urls and product.get("image_url"):
        image_urls = [product["image_url"]]

    # Image alt text: SEO-optimized with vendor, type, color
    color_name = product.get("color", "")
    alt_base = f"{vendor} {title}"
    alt_parts = [vendor, title]
    if type_da:
        alt_parts.append(type_da)
    if color_name:
        alt_parts.append(color_name)
    alt_seo = " — ".join([alt_base, type_da]) if type_da else alt_base

    for idx, img_url in enumerate(image_urls[:5]):
        try:
            if idx == 0:
                alt = alt_seo
            elif idx == 1:
                alt = f"{title} — bagside" if type_da in CLOTHING_TYPES else f"{title} — detalje"
            else:
                alt = f"{title} — billede {idx + 1}"
            shopify.add_image_by_url(product_id, img_url, alt_text=alt)
        except Exception as e:
            errors_log.append(f"Image {idx + 1}: {e}")

    # ── 6. Publish to ALL available channels (Danmark, stromstore.com, stromstore.us, etc.) ──
    if publications:
        for pub in publications:
            try:
                shopify.publish_product_single(product_id, pub["id"])
            except Exception as e:
                errors_log.append(f"Publishing ({pub['name']}): {e}")

    # ── 7. Add to brand collection (skip smart collections — they auto-match) ──
    # Reuse the brand_collection_gid we already found above
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
            # Smart collections auto-match products — not an actual error
            if "smart collection" not in err_str.lower() and "Can't manually add" not in err_str:
                errors_log.append(f"Collection ({matched_col['title']}): {e}")

    # ── 8. Create English translation ──
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
    except Exception as e:
        errors_log.append(f"Translation: {e}")

    return {
        "product_id": product_id,
        "title": title,
        "variants": len(variant_edges),
        "errors": errors_log,
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
        # Debug: vis token/store info
        token_preview = SHOPIFY_ACCESS_TOKEN[:6] + "..." + SHOPIFY_ACCESS_TOKEN[-4:] if len(SHOPIFY_ACCESS_TOKEN) > 10 else "(for kort)"
        st.caption(f"Store: `{SHOPIFY_STORE}`")
        st.caption(f"Token: `{token_preview}` ({len(SHOPIFY_ACCESS_TOKEN)} tegn)")
        st.caption(f"URL: `https://{SHOPIFY_STORE}.myshopify.com`")
    else:
        st.error("Mangler SHOPIFY_ACCESS_TOKEN")

    # Load Shopify data on first run (use a flag to avoid re-fetching)
    if "shopify_loaded" not in st.session_state:
        st.session_state.shopify_loaded = False
    if shopify_ok and not st.session_state.shopify_loaded:
        shopify = ShopifyGraphQL(SHOPIFY_STORE, SHOPIFY_ACCESS_TOKEN)
        with st.spinner("Henter data fra Shopify..."):
            try:
                st.session_state.existing_tags = shopify.fetch_all_tags()
                st.session_state.existing_vendors = shopify.fetch_all_vendors()
                st.session_state.publications = shopify.fetch_publications()
                st.session_state.collections = shopify.fetch_collections()
                # Fetch active product descriptions for AI style reference
                st.session_state.active_descriptions = shopify.fetch_active_products(limit=30)
                st.session_state.location_id = shopify.get_primary_location_id()
                st.session_state.metafield_defs = shopify.fetch_metafield_definitions("PRODUCT")
                st.session_state.shopify_loaded = True
            except Exception as e:
                st.error(f"Shopify-fejl: {e}")
                st.error(f"Fuld URL brugt: https://{SHOPIFY_STORE}.myshopify.com/admin/api/2024-10/graphql.json")

        st.caption(f"{len(st.session_state.existing_tags)} tags")
        st.caption(f"{len(st.session_state.existing_vendors)} vendors")
        st.caption(f"{len(st.session_state.publications)} kanaler")
        st.caption(f"{len(st.session_state.collections)} collections")
        st.caption(f"{len(st.session_state.metafield_defs)} metafield defs")

        # Debug: show metafield definitions
        if st.session_state.metafield_defs:
            with st.expander("Metafield definitions"):
                for mf in st.session_state.metafield_defs:
                    st.caption(f"`{mf['namespace']}.{mf['key']}` ({mf['type']}) — {mf['name']}")

    st.divider()

    eur_rate = st.number_input(
        "EUR → DKK", value=EUR_TO_DKK, step=0.01, format="%.2f", key="eur_rate",
    )

    st.divider()

    if st.button("Start forfra"):
        st.session_state.products = []
        st.session_state.step = "upload"
        st.session_state.push_results = []
        st.session_state.shopify_loaded = False
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
                # Convert PDF pages to images for Claude Vision
                pdf_images = extract_pdf_pages_as_images(pdf_bytes, dpi=150)

            # Pre-parse table structure for accurate size extraction (deterministic fallback)
            with st.spinner(f"Parser tabelstruktur fra {uploaded_file.name}..."):
                table_products = parse_invoice_tables(pdf_bytes)
                if table_products:
                    for tp in table_products:
                        sizes_str = ", ".join([f"{v['size']}({v['quantity']})" for v in tp['variants']]) if tp['variants'] else f"Total: {tp['total_qty']}"
                        st.caption(f"  ✓ {tp['style_code']} — {tp['designation']} {tp['color_original']}: {sizes_str}")
                else:
                    st.info("Ingen tabel-parser tilgængelig for dette format — Claude Vision udtrækker data direkte fra billedet")

            with st.spinner(f"Claude Vision analyserer {uploaded_file.name}..."):
                try:
                    products = extract_products_with_ai(
                        pdf_text, st.session_state.existing_tags,
                        pdf_images=pdf_images,
                        table_products=table_products,
                        active_descriptions=st.session_state.get("active_descriptions", []),
                    )
                    for p in products:
                        # Debug: warn if only 1 variant
                        variants = p.get("variants", [])
                        total_qty = sum(v.get("quantity", 0) for v in variants)
                        if len(variants) <= 1:
                            st.warning(f"⚠ {p.get('title', '?')}: Kun {len(variants)} størrelse(r) fundet! (total: {total_qty} stk)")
                        # Map type to Danish
                        p["product_type_da"] = map_type_danish(p.get("product_type", ""))
                        # Sort variants in logical size order
                        p["variants"] = sort_sizes(p.get("variants", []))
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
            # Search for images AND product details (material, description) from brand websites
            with st.spinner("Søger produktbilleder og detaljer via SKU..."):
                for p in all_products:
                    result = find_product_images_and_details(
                        p["vendor"], p.get("style_code", ""), p["title"], max_images=8
                    )
                    imgs = result.get("images", [])
                    scraped = result.get("details", {})

                    # Filter out model shots using Claude Vision
                    if imgs and ANTHROPIC_API_KEY:
                        st.caption(f"  ↳ Filtrerer {len(imgs)} billeder med AI (fjerner modelbilleder)...")
                        imgs = _filter_images_with_vision(imgs, p["title"], ANTHROPIC_API_KEY)
                        imgs = imgs[:5]  # Max 5 after filtering

                    p["image_urls"] = imgs
                    p["image_url"] = imgs[0] if imgs else ""

                    # Enrich with scraped data — only fill in MISSING fields
                    if scraped.get("material") and not p.get("material"):
                        # Translate common English material names to Danish
                        mat = scraped["material"]
                        mat_translations = [
                            ("cotton", "bomuld"), ("wool", "uld"), ("silk", "silke"),
                            ("linen", "hør"), ("polyamide", "polyamid"), ("viscose", "viskose"),
                            ("elastane", "elastan"), ("cashmere", "kashmir"),
                        ]
                        mat_da = mat
                        for en, da in mat_translations:
                            mat_da = re.sub(rf'\b{en}\b', da, mat_da, flags=re.IGNORECASE)
                        p["material"] = mat_da
                        st.caption(f"  ↳ Materiale fra brand-side: {mat_da}")

                    # Use scraped description as fallback if AI description is weak
                    if scraped.get("description_en") and (not p.get("details_en") or len(p.get("details_en", "")) < 20):
                        p["details_en"] = scraped["description_en"][:300]

            # Check for duplicate SKUs already in Shopify
            if shopify_ok:
                with st.spinner("Tjekker for eksisterende produkter i Shopify..."):
                    shopify_check = ShopifyGraphQL(SHOPIFY_STORE, SHOPIFY_ACCESS_TOKEN)
                    for p in all_products:
                        sku = p.get("style_code", "")
                        if sku:
                            existing = shopify_check.search_products_by_sku(sku)
                            if existing:
                                p["_duplicate_warning"] = existing
                                dupe_names = ', '.join([e['title'] + ' (' + e['status'] + ')' for e in existing])
                                st.warning(
                                    f"⚠ {p['title']} (SKU: {sku}) findes allerede i Shopify: {dupe_names}"
                                )

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

        # Build status indicator for expander header
        issues = []
        if not p.get("image_urls") and not p.get("image_url"):
            issues.append("📷")
        if not p.get("material") or p.get("material", "").lower() in ("ikke oplyst", "n/a", ""):
            issues.append("🧵")
        if p.get("retail_price_dkk", 0) == 0 or p.get("cost_price_eur", 0) == 0:
            issues.append("💰")
        if p.get("_duplicate_warning"):
            issues.append("⚠️")
        issue_str = " ".join(issues) + " " if issues else "✓ "

        with st.expander(
            f"{issue_str}**{p['vendor']}** — {p['title']} | {total_qty} stk | {p.get('retail_price_dkk', 0):.0f} DKK",
            expanded=(i == 0),
        ):
            # Show warnings at top of each product
            if p.get("_duplicate_warning"):
                dupes = p["_duplicate_warning"]
                dupe_list = ', '.join([d['title'] + ' (' + d['status'] + ')' for d in dupes])
                st.error(
                    f"⚠️ DUPLIKAT: SKU {p.get('style_code', '')} findes allerede i Shopify — {dupe_list}"
                )
            if not p.get("image_urls") and not p.get("image_url"):
                st.warning("📷 Ingen billeder fundet — produktet oprettes uden billeder")
            if not p.get("material") or p.get("material", "").lower() in ("ikke oplyst", "n/a", ""):
                st.info("🧵 Materiale mangler — kan tilføjes manuelt nedenfor")
            if p.get("retail_price_dkk", 0) > 15000:
                st.warning(f"💰 Høj pris: {p.get('retail_price_dkk', 0):.0f} DKK — tjek at kostpris er korrekt")
            if p.get("cost_price_eur", 0) == 0:
                st.error("💰 Kostpris er 0 — produktet kan ikke prissættes korrekt")

            preview_col, data_col = st.columns([1, 1])

            # ── Preview ──
            with preview_col:
                st.markdown("##### Preview")

                # Images
                img_urls = p.get("image_urls", [])
                if not img_urls and p.get("image_url"):
                    img_urls = [p["image_url"]]
                if img_urls:
                    st.image(img_urls[0], width=200)
                    if len(img_urls) > 1:
                        st.caption(f"{len(img_urls)} billeder fundet")
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
                _rate = st.session_state.get("eur_rate", EUR_TO_DKK)
                cost_dkk = round(p.get("cost_price_eur", 0) * _rate, 2)

                st.text(f"Handle:    {make_handle(p['vendor'], p['title'])}")
                st.text(f"SKU:       {p.get('style_code', '')}")
                st.text(f"Type:      {p.get('product_type_da', '')}")
                st.text(f"Vendor:    {p['vendor']}")
                st.text(f"Gender:    {p.get('gender', 'N/A')}")
                st.text(f"Farve:     {p.get('color', 'N/A')} (original: {p.get('color_original', p.get('color', 'N/A'))})")
                st.text(f"Materiale: {p.get('material', 'N/A')}")
                st.text(f"Cost:      {cost_dkk:.2f} DKK (€{p.get('cost_price_eur', 0):.2f})")
                st.text(f"Retail:    {p.get('retail_price_dkk', 0):.0f} DKK")
                st.text(f"Season:    {p.get('season', 'N/A')}")
                st.text(f"Origin:    {p.get('country_of_origin', 'N/A')}")

                sizes = ", ".join([f"{v['size']}({v['quantity']})" for v in p.get("variants", [])])
                st.text(f"Sizes:     {sizes}")

                tags_str = ", ".join(p.get("computed_tags", []))
                st.text(f"Tags:      {tags_str}")

                if p.get("details"):
                    st.text(f"Detaljer:  {p['details']}")

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

            ec4, ec5, ec6 = st.columns(3)

            with ec4:
                new_color = st.text_input(
                    "Farve (Color-Name)", value=p.get("color", ""), key=f"color_{i}",
                )
                st.session_state.products[i]["color"] = new_color

            with ec5:
                gender_options = ["Women", "Men", "Unisex"]
                current_gender = p.get("gender", "Women")
                gender_idx = gender_options.index(current_gender) if current_gender in gender_options else 0
                new_gender = st.selectbox(
                    "Køn", options=gender_options, index=gender_idx, key=f"gender_{i}",
                )
                st.session_state.products[i]["gender"] = new_gender

            with ec6:
                new_material = st.text_input(
                    "Materiale", value=p.get("material", ""), key=f"material_{i}",
                )
                st.session_state.products[i]["material"] = new_material

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

    if not products:
        st.warning("Ingen produkter at oprette.")
        st.session_state.step = "upload"
        st.rerun()

    for i, p in enumerate(products):
        progress.progress((i + 1) / len(products))

        with st.spinner(f"Opretter {p['title']}..."):
            try:
                result = push_product_to_shopify(
                    shopify=shopify,
                    product=p,
                    eur_rate=st.session_state.get("eur_rate", EUR_TO_DKK),
                    publications=st.session_state.publications,
                    collections=st.session_state.collections,
                    location_id=st.session_state.location_id or "",
                    metafield_defs=st.session_state.metafield_defs,
                )
                result_entry = {"status": "ok", "title": p["title"], "id": result["product_id"]}
                if result.get("errors"):
                    result_entry["warnings"] = result["errors"]
                results.append(result_entry)
                if result.get("errors"):
                    st.warning(f"⚠ {p['title']} (oprettet med advarsler)")
                else:
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
        if r.get("warnings"):
            st.warning(f"⚠ {r['title']} — advarsler:")
            for w in r["warnings"]:
                st.caption(f"  · {w}")
        else:
            st.success(f"✓ {r['title']}")
    for r in fail:
        st.error(f"✗ {r['title']}: {r['error']}")

    if st.button("Importer flere", type="primary"):
        st.session_state.products = []
        st.session_state.push_results = []
        st.session_state.step = "upload"
        # Don't reset shopify_loaded — keep cached data for next import
        st.rerun()
