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


def parse_invoice_tables(pdf_bytes: bytes) -> list[dict]:
    """
    Parse product lines from invoice PDF using fitz table extraction.
    Returns structured product data with EXACT size→quantity mappings.
    This is deterministic — no AI guessing needed for sizes.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    products = []

    for page in doc:
        try:
            tables = page.find_tables()
        except Exception:
            continue

        for table in tables.tables:
            rows = table.extract()
            if not rows or len(rows) < 2:
                continue

            # Check if this is a product table (header row contains "Code article" or "Designation")
            header = rows[0]
            header_text = " ".join([str(c or "") for c in header]).lower()
            if "code article" not in header_text and "designation" not in header_text:
                continue

            # Parse product lines: each product spans 2-3 rows
            # Row pattern:
            #   [style_code, designation, ..., qty, unit_price, rem, unit_net, total]
            #   [Couleur, size1, size2, ..., Total, ...]
            #   [color_name, qty1, qty2, ..., total_qty, ...]
            i = 1  # skip header
            while i < len(rows):
                row = rows[i]
                cells = [str(c or "").strip() for c in row]

                # Skip empty rows
                if not any(cells):
                    i += 1
                    continue

                # Detect product line: first cell looks like a style code (alphanumeric, 6+ chars)
                style_code = cells[0] if cells else ""
                if not style_code or len(style_code) < 4 or not re.match(r'^[A-Za-z0-9]', style_code):
                    i += 1
                    continue

                # Check if this is actually a product row (has designation in cell 1)
                designation = cells[1] if len(cells) > 1 else ""
                # Clean up designation (remove BL/Commande/Facture references line by line)
                if designation:
                    # Split into lines and keep only product-type lines
                    lines = designation.split("\n")
                    clean_lines = []
                    for line in lines:
                        line_strip = line.strip()
                        # Skip order/invoice reference lines
                        if any(skip in line_strip for skip in ["BL client", "Commande", "Facture", "N°"]):
                            continue
                        if line_strip:
                            clean_lines.append(line_strip)
                    designation = " ".join(clean_lines).strip()

                if not designation or designation.lower() in ("couleur", ""):
                    i += 1
                    continue

                # Extract cost and quantity from the product row
                cost_eur = 0
                total_qty = 0
                for cell in cells[2:]:
                    cell_clean = cell.replace(",", ".").replace(" ", "")
                    try:
                        val = float(cell_clean)
                        # Heuristic: unit net price is typically 20-200 range
                        if 10 < val < 500 and cost_eur == 0:
                            cost_eur = val
                    except (ValueError, TypeError):
                        pass

                # Look for Qty in the dedicated column (usually column 7 based on header)
                qty_col = None
                for hi, h in enumerate(header):
                    if str(h or "").strip().lower() in ("qté", "qty", "quantité"):
                        qty_col = hi
                        break
                if qty_col and qty_col < len(cells):
                    try:
                        total_qty = int(cells[qty_col])
                    except (ValueError, TypeError):
                        pass

                # Extract unit net price from the "Unit. net" column
                unitnet_col = None
                for hi, h in enumerate(header):
                    if "unit. net" in str(h or "").lower() or "net" in str(h or "").lower():
                        unitnet_col = hi
                        break
                if unitnet_col and unitnet_col < len(cells):
                    try:
                        cost_eur = float(cells[unitnet_col].replace(",", ".").replace(" ", ""))
                    except (ValueError, TypeError):
                        pass

                # Now look for size/color rows (next 1-2 rows)
                color = ""
                variants = []

                if i + 2 < len(rows):
                    size_row = rows[i + 1]
                    qty_row = rows[i + 2]

                    size_cells = [str(c or "").strip() for c in size_row]
                    qty_cells = [str(c or "").strip() for c in qty_row]

                    # size_row[0] should be "Couleur", size_row[1:] are size names
                    # qty_row[0] is the color name, qty_row[1:] are quantities
                    if size_cells and size_cells[0].lower() == "couleur":
                        color = qty_cells[0] if qty_cells else ""

                        # Match sizes to quantities by column position
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

                        i += 3  # skip product + size + qty rows
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
                    })

    return products


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

    # Acne Studios exception
    if "acne" in vendor.lower():
        tags.append("acne-products")

    # Add ONLY AI-suggested tags that match existing store tags — be selective
    # Filter out English product type names (we already have Danish ones)
    english_type_tags = {
        "Shirt", "Trouser", "Pants", "Knit", "Jacket", "Coat", "Blazer",
        "Dress", "Skirt", "Top", "Blouse", "Shorts", "Hoodie", "Sweatshirt",
        "Vest", "Polo", "Sneaker", "Sandal", "Boot", "Loafer", "Shoe",
        "Bag", "Scarf", "Hat", "Cap", "Belt", "Gloves",
    }
    ai_tags = product.get("ai_tags", [])
    existing = set(st.session_state.existing_tags)
    for t in ai_tags:
        if t in english_type_tags:
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

    lines.append(f"<p>{title} fra {vendor}. {detail_text}</p>")

    # Bullet list: Farve + Materiale (matches reference product format)
    bullet_items = []
    if color:
        bullet_items.append(f"<li>Farve: {color}</li>")
    if material:
        bullet_items.append(f"<li>Materiale: {material}</li>")
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

def extract_products_with_ai(pdf_text: str, existing_tags: list[str], pdf_images: list[str] = None, table_products: list[dict] = None) -> list[dict]:
    """
    Hybrid extraction:
    - Sizes/quantities come from table_products (deterministic, 100% accurate)
    - Metadata (title, color, material, tags, etc.) comes from AI enrichment
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tag_list = ", ".join(existing_tags) if existing_tags else "(ingen eksisterende tags)"

    # Build pre-parsed product summary for the AI
    table_summary = ""
    if table_products:
        table_summary = "\n\nPRE-PARSED PRODUKTDATA (størrelser og antal er ALLEREDE udtrukket korrekt — brug disse DIREKTE):\n"
        for tp in table_products:
            sizes_str = ", ".join([f"{v['size']}({v['quantity']})" for v in tp['variants']])
            table_summary += f"  - {tp['style_code']} | {tp['designation']} | Farve: {tp['color_original']} | €{tp['cost_price_eur']} | Størrelser: {sizes_str} | Total: {tp['total_qty']}\n"
        table_summary += "\nKRITISK: Brug PRÆCIS disse størrelser og antal i dit JSON-output. ÆNDR DEM IKKE.\n"

    system_prompt = f"""Du er en Shopify-produktekspert for STRØM (stromstore.dk), en premium skandinavisk modebutik.

Berig produktdata fra leverandørfakturaer med metadata og returnér struktureret JSON.

VIGTIG: Størrelser og antal er ALLEREDE udtrukket fra fakturaen. Du skal IKKE ændre dem.
Du skal tilføje: titel, farve-oversættelse, produkttype, køn, materiale, detaljer, tags.

TITEL-REGLER (KRITISK):
- Titlen = Produktnavn + ORIGINAL faktura-farvenavn (PRÆCIS som det står på fakturaen)
- Format: "Produktnavn Farvenavn" (f.eks. "Chemise Vichy Bleu", "Pantalon Blanc Cassé")
- Brug det PRÆCISE produktnavn fra fakturaen + den PRÆCISE farve fra fakturaen (UOVERSAT)
- Ingen CAPS LOCK — brug Title Case (f.eks. "Vichy Bleu", IKKE "VICHY BLEU")

FARVE-REGLER FOR "color"-FELTET (gælder KUN for "color"-feltet, IKKE for titlen):
- "color"-feltet bruges til Color-Name metafield i Shopify
- Oversæt til simple danske/engelske farvenavne:
  BLANC CASSE / ECRU → Off White
  GRIS CHINE / GRIS CLAIR → Lysegrå
  CREME MOULI / CREAM MOULINE → Creme
  VICHY BLEU → Blåternet
  NOIR → Sort
  BLEU / BLEU NUIT → Mørkeblå
  BLEU CLAIR → Lyseblå
  ROUGE → Rød
  VERT / VERT FONCE → Mørkegrøn
  MARRON → Brun, BEIGE → Beige, ROSE → Lyserød
  CAMEL → Kamel, ANTHRACITE → Antracitgrå, KAKI → Armygrøn
  BORDEAUX → Bordeaux, TAUPE → Gråbrun

PRODUKTDETALJER (2-3 SÆTNINGER, KUN FYSISK BESKRIVELSE):
- Beskriv KUN: pasform, snit, krave, ærmer, lukning, lommer, mønster
- FORBUDTE ORD: "fremgår ikke", "kan ikke udledes", "perfekt til", "ideel til",
  "elegant", "raffineret", "tidløs", "typisk for mærket", "kollektionen"
- Generisk men korrekt beskrivelse baseret på produkttypen er OK:
  Skjorte → "Skjorte med knapper foran og krave. Lange ærmer med manchetter."
  Bukser → "Bukser med almindelig pasform. Lynlås og knap i livet."
- Gem dansk i "details" og engelsk i "details_en"

MATERIALE:
- Udtræk fra fakturaen: COTON → bomuld, LAINE → uld, SOIE → silke, LIN → hør
- Hvis IKKE på fakturaen → tom streng ""

PRODUKTTYPE (engelsk): Pantalon → "Trouser", Chemise → "Shirt", Pull → "Knit", etc.

KØN: "Men", "Women" eller "Unisex" (standard "Women" for American Vintage)

SÆSON: "E26" → "SS26", "H26" → "FW26"

EKSISTERENDE TAGS: {tag_list}

Returnér KUN valid JSON array. Brug PRÆCIS de størrelser/antal fra pre-parsed data:
[
  {{
    "style_code": "original artikelkode",
    "title": "Produktnavn OriginalFarve",
    "vendor": "Brand Name",
    "product_type": "engelsk type",
    "gender": "Men/Women/Unisex",
    "color": "simpelt oversat farvenavn",
    "color_original": "PRÆCIS farvenavn fra fakturaen",
    "material": "dansk materiale i %",
    "details": "2-3 sætninger dansk",
    "details_en": "2-3 sentences English",
    "country_of_origin": "ISO kode eller tom",
    "hs_code": "",
    "season": "SS26/FW26",
    "cost_price_eur": enhedspris,
    "ai_tags": ["relevante", "tags"],
    "variants": [{{"size": "S", "quantity": 2}}, {{"size": "M", "quantity": 3}}]
  }}
]"""

    user_content = f"""Berig disse produkter fra leverandørfaktura med metadata.
{table_summary}
FAKTURA-TEKST (for vendor, materiale, sæson, land osv.):
{pdf_text}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": user_content,
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
        # Fix descriptions: remove forbidden phrases
        for field in ("details", "details_en"):
            text = p.get(field, "")
            if text:
                text = _clean_description(text)
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


def _clean_description(text: str) -> str:
    """Remove forbidden phrases from AI-generated descriptions."""
    if not text:
        return text

    # Forbidden phrases — remove sentences containing these
    forbidden = [
        "fremgår ikke", "kan ikke udledes", "ikke tilgængelig",
        "ikke muligt at afgøre", "ikke muligt at fastslå",
        "style den med", "perfekt til", "ideel til", "passer godt til",
        "typisk for mærket", "kendetegnet ved", "kollektionen",
        "fakturaen", "kilden", "manglende information",
        "ikke angivet", "ikke specificeret", "kan ikke bestemmes",
        "fra fakturaen", "af fakturaen", "på fakturaen",
        "information er ikke", "data er ikke", "oplysninger er ikke",
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
    """Generate a generic but correct description based on product type."""
    fallbacks = {
        "Skjorter": "Skjorte med knapper foran og krave. Lange ærmer med manchetter.",
        "Bukser": "Bukser med almindelig pasform. Lynlås og knap i livet.",
        "T-Shirts": "T-shirt med rund hals og korte ærmer.",
        "Strik": "Striktrøje med rund hals og lange ærmer.",
        "Jakker": "Jakke med knapper eller lynlås foran.",
        "Blazere": "Blazer med reverskrave og to knapper foran.",
        "Kjoler": "Kjole med almindelig pasform.",
        "Nederdele": "Nederdel med almindelig pasform.",
        "Toppe": "Top med rund hals.",
        "Bluser": "Bluse med rund hals og lange ærmer.",
        "Hoodies": "Hoodie med hætte og kængurulomme foran.",
        "Sweatshirts": "Sweatshirt med rund hals og lange ærmer.",
        "Shorts": "Shorts med almindelig pasform. Lynlås og knap i livet.",
        "Poloer": "Polo med krave og knaplukning foran. Korte ærmer.",
        "Veste": "Vest uden ærmer.",
        "Sneakers": "Sneakers med snørebånd.",
        "Sandaler": "Sandaler med åben tå.",
        "Støvler": "Støvler med snørebånd eller lynlås.",
        "Loafers": "Loafers uden lukning.",
        "Sko": "Sko med klassisk pasform.",
        "Tasker": "Taske med skulderrem.",
        "Rygsække": "Rygsæk med justerbare stropper.",
        "Tørklæder": "Tørklæde i blød kvalitet.",
        "Bælter": "Bælte med spænde.",
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
    elif "maison margiela" in vendor_lower or "margiela" in vendor_lower:
        brand_search_url = f"https://www.maisonmargiela.com/search?q={sku_encoded}"
        brand_domain = "maisonmargiela.com"
    elif "a.p.c" in vendor_lower or "apc" in vendor_lower:
        brand_search_url = f"https://www.apc.fr/en/search?q={sku_encoded}"
        brand_domain = "apc.fr"

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

    # ── Strategy 2: Multi-brand retailers — same two-step approach ──
    retailer_searches = [
        (f"https://www.ssense.com/en-dk/search?q={sku_encoded}", "ssense.com"),
        (f"https://www.farfetch.com/dk/shopping/search/items/?q={sku_encoded}", "farfetch.com"),
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

    # ── Strategy 3: Direct brand CDN patterns ──
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
    From a specific product page, extract PACKSHOT images (no models).
    Collects all candidate images, scores them (packshot vs model), returns top packshots.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    all_candidates = []
    seen_urls = set()

    def _normalize_image_url(url: str) -> str:
        """Normalize URL for deduplication — strip size params, query strings."""
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        parsed = urlparse(url)
        # Remove common resize/width/height params
        if parsed.query:
            params = parse_qs(parsed.query)
            # Remove size/quality params that create duplicates
            for param in ["width", "height", "w", "h", "quality", "q", "size",
                          "format", "fit", "crop", "auto", "dpr"]:
                params.pop(param, None)
            clean_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(query=clean_query))
        return url

    def _collect(url: str, source: str = ""):
        if not url or len(url) < 15:
            return
        if url.startswith("//"):
            url = "https:" + url
        # Normalize for dedup comparison
        norm_url = _normalize_image_url(url)
        if norm_url in seen_urls:
            return
        if not _is_valid_product_image(url):
            return
        seen_urls.add(norm_url)
        all_candidates.append({"url": url, "source": source})

    try:
        response = requests.get(product_url, headers=headers, timeout=10)
        if not response.ok:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # Collect from JSON-LD
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
                                _collect(img_url, "jsonld")
            except Exception:
                continue

        # Collect from OG tags
        for og in soup.find_all("meta", property="og:image"):
            if og.get("content"):
                _collect(og["content"], "og")

        # Collect from img tags
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "") or img.get("data-zoom-image", "") or ""
            alt = (img.get("alt", "") or "").lower()

            if not src or len(src) < 10:
                continue

            width = img.get("width", "")
            if width and width.isdigit() and int(width) < 150:
                continue

            src_lower = src.lower()
            has_sku = sku_lower in src_lower or sku_lower.replace("-", "") in src_lower.replace("-", "")
            has_sku_alt = sku_lower in alt
            is_product_img = any(kw in src_lower for kw in ["product", "catalog", "media/", "cdn.shopify", "images/products"])

            if has_sku or has_sku_alt or is_product_img:
                full_src = src
                if src.startswith("/"):
                    full_src = urljoin(product_url, src)
                _collect(full_src, "img")

    except Exception:
        pass

    if not all_candidates:
        return []

    # Score images: PACKSHOTS first, model shots last
    scored = []
    for c in all_candidates:
        url_lower = c["url"].lower()
        score = 0

        # PACKSHOT indicators (higher = better)
        packshot_keywords = ["flat", "packshot", "still", "ghost", "product",
                             "detail", "close", "cut-out", "cutout", "_e", "_e_",
                             "pack", "lay", "front", "back"]
        for kw in packshot_keywords:
            if kw in url_lower:
                score += 10

        # MODEL indicators (lower = worse)
        model_keywords = ["model", "look", "worn", "outfit", "lifestyle",
                          "campaign", "editorial", "runway", "wearing",
                          "_m_", "_m.", "mannequin", "styled"]
        for kw in model_keywords:
            if kw in url_lower:
                score -= 20

        # SKU in URL is a good sign (right product)
        if sku_lower in url_lower:
            score += 5

        scored.append((score, c["url"]))

    # Sort by score (highest first) and return top packshots
    scored.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url in scored[:max_images]]


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
    seo_title = f"{title} | STRØM"
    if len(seo_title) > 70:
        seo_title = f"{title[:60]} | STRØM"
    seo_desc = f"Køb {title} fra {vendor} hos STRØM. Premium skandinavisk mode."
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

    created = shopify.create_product(product_input)
    product_id = created["id"]

    # Error collector for debugging
    errors_log = []

    # ── 2. Update variant prices (only price — SKU/weight/cost via inventoryItemUpdate) ──
    variant_edges = created.get("variants", {}).get("edges", [])

    # Build a size→variant_node map from created variants
    variant_map = {}
    for edge in variant_edges:
        node = edge["node"]
        for opt in node.get("selectedOptions", []):
            if opt["name"] == "Size":
                variant_map[opt["value"]] = node

    style_code = product.get("style_code", "")

    # ProductVariantsBulkInput only accepts: id, price, compareAtPrice, barcode, inventoryPolicy, metafields, optionValues
    variant_updates = []
    for v in product.get("variants", []):
        size = v["size"]
        node = variant_map.get(size)
        if not node:
            continue
        variant_updates.append({
            "id": node["id"],
            "price": str(retail_price),
        })

    updated_variants = []
    if variant_updates:
        try:
            updated_variants = shopify.update_variants_bulk(product_id, variant_updates)
        except Exception as e:
            errors_log.append(f"Variant price update: {e}")

    # ── 3. Set SKU, cost, weight, inventory qty, origin per variant via inventoryItemUpdate ──
    original_variants = product.get("variants", [])
    size_to_qty = {v["size"]: v.get("quantity", 0) for v in original_variants}
    country = product.get("country_of_origin", "")
    hs_code = product.get("hs_code", "")

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

        # Update inventory item: SKU, cost, country, HS code (no weight — not available from invoices)
        try:
            shopify.update_inventory_item(
                inventory_item_id=inv_item_id,
                cost=cost_dkk,
                sku=sku,
                country_code=country,
                hs_code=hs_code,
                tracked=True,
            )
        except Exception as e:
            errors_log.append(f"Inventory item ({var_size}): {e}")

        # Set quantity
        qty = size_to_qty.get(var_size, 0)
        if qty > 0 and location_id:
            try:
                shopify.set_inventory_quantity(inv_item_id, location_id, qty)
            except Exception as e:
                errors_log.append(f"Inventory qty ({var_size}): {e}")

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

    for idx, img_url in enumerate(image_urls[:5]):
        try:
            alt = f"{title} - billede {idx + 1}" if idx > 0 else title
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

            # Pre-parse table structure for accurate size extraction
            with st.spinner(f"Parser tabelstruktur fra {uploaded_file.name}..."):
                table_products = parse_invoice_tables(pdf_bytes)
                if table_products:
                    for tp in table_products:
                        sizes_str = ", ".join([f"{v['size']}({v['quantity']})" for v in tp['variants']])
                        st.caption(f"  ✓ {tp['style_code']} — {tp['designation']} {tp['color_original']}: {sizes_str}")
                else:
                    st.warning("Kunne ikke parse tabel — AI vil forsøge at udtrække størrelser")

            with st.spinner(f"AI beriger metadata fra {uploaded_file.name}..."):
                try:
                    products = extract_products_with_ai(
                        pdf_text, st.session_state.existing_tags,
                        table_products=table_products,
                    )
                    for p in products:
                        # Debug: warn if only 1 variant
                        variants = p.get("variants", [])
                        total_qty = sum(v.get("quantity", 0) for v in variants)
                        if len(variants) <= 1:
                            st.warning(f"⚠ {p.get('title', '?')}: Kun {len(variants)} størrelse(r) fundet! (total: {total_qty} stk)")
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
            # Search for images AND product details (material, description) from brand websites
            with st.spinner("Søger produktbilleder og detaljer via SKU..."):
                for p in all_products:
                    result = find_product_images_and_details(
                        p["vendor"], p.get("style_code", ""), p["title"], max_images=5
                    )
                    imgs = result.get("images", [])
                    scraped = result.get("details", {})

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
