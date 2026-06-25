"""
Invoice table parsing — deterministic extraction from PDF tables and text.
Ported from app.py — parse_invoice_tables, _parse_tables_structured,
_parse_american_vintage_table, _parse_apc_table, _parse_text_based,
_parse_carhartt_text, _parse_generic_text.
"""

import re

import fitz  # PyMuPDF

from app.services.pdf_service import detect_invoice_currency


# ---------------------------------------------------------------------------
# Default EUR→DKK rate — callers can override via parameter
# ---------------------------------------------------------------------------
DEFAULT_EUR_TO_DKK = 7.46


def parse_invoice_tables(pdf_bytes: bytes, eur_to_dkk: float = DEFAULT_EUR_TO_DKK) -> list[dict]:
    """
    Parse product lines from invoice PDF.
    Supports multiple invoice formats:
    - American Vintage: structured tables with Couleur/size rows
    - A.P.C.: product blocks with size grid tables
    - Carhartt WIP / generic: text-based parsing when no tables found

    Returns structured product data with EXACT size->quantity mappings.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"

        # Detect currency
        currency = detect_invoice_currency(full_text)

        # Try table-based extraction first
        products = _parse_tables_structured(doc, currency, eur_to_dkk)

        # If tables didn't work, try text-based parsing
        if not products:
            products = _parse_text_based(full_text, currency, eur_to_dkk)

        return products
    finally:
        doc.close()


def _parse_tables_structured(doc, currency: str, eur_to_dkk: float) -> list[dict]:
    """Parse invoices that have proper table structures (American Vintage, A.P.C.)."""
    products = []
    used_skus: set[str] = set()  # Track which SKUs have been matched to a table

    for page in doc:
        try:
            tables = page.find_tables()
        except Exception:
            continue

        page_text = page.get_text()

        # -- Try American Vintage format (header row: "Code article", "Designation") --
        for table in tables.tables:
            rows = table.extract()
            if not rows or len(rows) < 2:
                continue

            header = rows[0]
            header_text = " ".join([str(c or "") for c in header]).lower()
            if "code article" in header_text or "designation" in header_text:
                products.extend(_parse_american_vintage_table(rows, header, currency, eur_to_dkk))
                continue

            # -- Try A.P.C. format (size grid: "Color", "S", "M", "L", "XL") --
            if any(str(c or "").strip().lower() == "color" for c in header):
                apc_product = _parse_apc_table(rows, page_text, currency, used_skus, eur_to_dkk)
                if apc_product:
                    used_skus.add(apc_product["style_code"])
                    products.append(apc_product)

    return products


def _parse_american_vintage_table(
    rows: list, header: list, currency: str, eur_to_dkk: float
) -> list[dict]:
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

        cost = 0.0
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
        cost_eur = cost / eur_to_dkk if currency == "DKK" else cost

        color = ""
        variants: list[dict] = []
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
            # Deduplicate: if same SKU already exists, merge variants (sum quantities)
            existing = next((p for p in products if p["style_code"] == style_code), None)
            if existing:
                # Merge variants: sum quantities for matching sizes, add new sizes
                existing_sizes = {v["size"]: v for v in existing["variants"]}
                for v in variants:
                    if v["size"] in existing_sizes:
                        existing_sizes[v["size"]]["quantity"] += v["quantity"]
                    else:
                        existing["variants"].append(v)
                existing["total_qty"] = sum(v["quantity"] for v in existing["variants"])
            else:
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


def _parse_apc_table(
    rows: list, page_text: str, currency: str, used_skus: set | None = None,
    eur_to_dkk: float = DEFAULT_EUR_TO_DKK,
) -> dict | None:
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
    variants: list[dict] = []

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
    cost = 0.0
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
    )  # noqa: F841 — kept for documentation; price_match2 below is the effective extractor
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

    cost_eur = cost / eur_to_dkk if currency == "DKK" else cost

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


def _parse_text_based(full_text: str, currency: str, eur_to_dkk: float) -> list[dict]:
    """
    Fallback: parse invoices from raw text when no tables are found.
    Handles Carhartt WIP and similar text-based invoice formats.

    Carhartt format example:
    25VA051691 I036159 3ONXX S/S Mello Knit Shirt 4 615,00 2.460,00
    Intra.61051000 CO: TR
    Cotton Knit, 12 gauge Mello Stripe, Black ---
    Net: 0,36 - 0,38 kg
    """
    products: list[dict] = []

    # Detect vendor from text
    text_lower = full_text.lower()

    # -- Carhartt WIP format --
    if "carhartt" in text_lower or "work in progress" in text_lower:
        products = _parse_carhartt_text(full_text, currency, eur_to_dkk)

    # -- Generic text-based fallback --
    if not products:
        products = _parse_generic_text(full_text, currency)

    return products


def _parse_carhartt_text(full_text: str, currency: str, eur_to_dkk: float) -> list[dict]:
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
    products: list[dict] = []
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
            order_no = lines[i - 1] if i > 0 else ""  # noqa: F841
            color_code = lines[i + 1] if i + 1 < len(lines) else ""
            name = lines[i + 2] if i + 2 < len(lines) else ""
            qty_str = lines[i + 3] if i + 3 < len(lines) else ""
            price_str = lines[i + 4] if i + 4 < len(lines) else ""
            total_str = lines[i + 5] if i + 5 < len(lines) else ""  # noqa: F841

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

            cost_eur = unit_price / eur_to_dkk if currency == "DKK" else unit_price

            current_product: dict = {
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
