import streamlit as st
import json
import fitz  # pymupdf
import anthropic
import requests
import re
from io import BytesIO
from image_scraper import scrape_images_for_product
from shopify_client import ShopifyClient

# ─── Page Config ───
st.set_page_config(
    page_title="Ström Store — Produkt Import",
    page_icon="🏷️",
    layout="wide",
)

# ─── Secrets / Config ───
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")
SHOPIFY_STORE = st.secrets.get("SHOPIFY_STORE", "stroemstore")
SHOPIFY_ACCESS_TOKEN = st.secrets.get("SHOPIFY_ACCESS_TOKEN", "")

# ─── Session State Init ───
if "products" not in st.session_state:
    st.session_state.products = []
if "pdf_text" not in st.session_state:
    st.session_state.pdf_text = ""
if "step" not in st.session_state:
    st.session_state.step = "upload"
if "push_results" not in st.session_state:
    st.session_state.push_results = []


# ─── Helper: Extract text from PDF ───
def extract_pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text


# ─── Helper: AI Extract Products ───
def extract_products_with_ai(pdf_text: str) -> list[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = """You are a product data extraction specialist for Ström Store, a Scandinavian fashion retailer.

Your job is to extract product data from supplier invoices/delivery notes and structure it for Shopify import.

RULES:
- Extract EVERY product line from the invoice
- Keep product titles/names EXACTLY as they appear in the source (do not translate)
- For each product, extract all available size variants with their quantities
- The prices on the invoice are WHOLESALE/COST prices — put them in "cost_price". Leave "retail_price" as null (the user sets this manually)
- Extract: article code/SKU, product name, color, material/composition, country of origin, sizes with quantities
- Identify the brand/vendor from the invoice header
- Identify the season if mentioned
- Generate appropriate tags (brand, category, season, material)
- Write a short, professional product description in English suitable for a premium fashion e-commerce store
- Generate SEO title and meta description in English

Return ONLY valid JSON array with this structure per product:
[
  {
    "sku": "article code from invoice",
    "title": "product name exactly as on invoice",
    "vendor": "brand name",
    "product_type": "category (e.g. Jacket, T-Shirt, Trousers, Shorts, Knitwear, Shirt)",
    "description": "professional product description for e-commerce (English, 2-3 sentences)",
    "color": "color name",
    "material": "material composition if available",
    "country_of_origin": "country if available",
    "season": "season code if available",
    "cost_price": wholesale unit price as number,
    "retail_price": null,
    "compare_at_price": null,
    "variants": [
      {"size": "S", "quantity": 2},
      {"size": "M", "quantity": 3}
    ],
    "tags": ["tag1", "tag2"],
    "seo_title": "SEO optimized title (max 70 chars)",
    "seo_description": "Meta description (max 160 chars)",
    "image_urls": []
  }
]"""

    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"Extract all products from this supplier invoice text:\n\n{pdf_text}",
            }
        ],
        system=system_prompt,
    )

    response_text = message.content[0].text

    # Extract JSON from response (handle markdown code blocks)
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response_text)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_str = response_text.strip()

    return json.loads(json_str)


# ─── UI ───

st.title("Ström Store — Produkt Import")
st.caption("Upload leverandør-faktura → Review produkter → Push til Shopify")

# Sidebar with settings
with st.sidebar:
    st.header("Indstillinger")
    if not ANTHROPIC_API_KEY:
        st.error("Mangler ANTHROPIC_API_KEY i secrets")
    else:
        st.success("Claude API ✓")
    if not SHOPIFY_ACCESS_TOKEN:
        st.error("Mangler SHOPIFY_ACCESS_TOKEN i secrets")
    else:
        st.success("Shopify API ✓")

    st.divider()
    st.caption(f"Butik: {SHOPIFY_STORE}.myshopify.com")

    if st.button("🔄 Start forfra"):
        st.session_state.products = []
        st.session_state.pdf_text = ""
        st.session_state.step = "upload"
        st.session_state.push_results = []
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
        help="Upload én eller flere PDF-fakturaer fra leverandører",
    )

    if uploaded_files and st.button("📄 Udtræk produkter", type="primary"):
        all_products = []

        for uploaded_file in uploaded_files:
            with st.spinner(f"Læser {uploaded_file.name}..."):
                pdf_bytes = uploaded_file.read()
                pdf_text = extract_pdf_text(pdf_bytes)

            with st.spinner(f"AI udtrækker produktdata fra {uploaded_file.name}..."):
                try:
                    products = extract_products_with_ai(pdf_text)
                    st.success(
                        f"✓ {uploaded_file.name}: {len(products)} produkter fundet"
                    )
                    all_products.extend(products)
                except Exception as e:
                    st.error(f"Fejl ved {uploaded_file.name}: {str(e)}")

        if all_products:
            st.session_state.products = all_products
            st.session_state.step = "review"
            st.rerun()


# ════════════════════════════════════════════
# STEP 2: Review & Edit Products
# ════════════════════════════════════════════
elif st.session_state.step == "review":
    st.header(f"2. Review produkter ({len(st.session_state.products)} fundet)")
    st.info(
        "Gennemgå produkterne nedenfor. Udfyld udsalgspriser og ret evt. titler eller tags."
    )

    # Image scraping option
    col_img1, col_img2 = st.columns([3, 1])
    with col_img1:
        st.caption(
            "Vil du forsøge at hente produktbilleder automatisk fra leverandør-websites?"
        )
    with col_img2:
        if st.button("🖼️ Hent billeder"):
            for i, product in enumerate(st.session_state.products):
                with st.spinner(
                    f"Søger billeder til {product['sku']}..."
                ):
                    urls = scrape_images_for_product(
                        product["vendor"], product["sku"], product["title"]
                    )
                    if urls:
                        st.session_state.products[i]["image_urls"] = urls
            st.rerun()

    st.divider()

    # Product cards
    products_to_remove = []

    for i, product in enumerate(st.session_state.products):
        with st.expander(
            f"**{product.get('vendor', '?')}** — {product.get('title', 'Ukendt')} ({product.get('sku', '')})",
            expanded=(i == 0),
        ):
            col1, col2 = st.columns([2, 1])

            with col1:
                product["title"] = st.text_input(
                    "Titel", value=product.get("title", ""), key=f"title_{i}"
                )
                product["description"] = st.text_area(
                    "Beskrivelse",
                    value=product.get("description", ""),
                    key=f"desc_{i}",
                    height=100,
                )
                product["tags"] = st.text_input(
                    "Tags (kommasepareret)",
                    value=", ".join(product.get("tags", [])),
                    key=f"tags_{i}",
                )

            with col2:
                product["vendor"] = st.text_input(
                    "Vendor/Brand", value=product.get("vendor", ""), key=f"vendor_{i}"
                )
                product["product_type"] = st.text_input(
                    "Produkttype",
                    value=product.get("product_type", ""),
                    key=f"type_{i}",
                )
                product["color"] = st.text_input(
                    "Farve", value=product.get("color", ""), key=f"color_{i}"
                )
                product["material"] = st.text_input(
                    "Materiale", value=product.get("material", ""), key=f"mat_{i}"
                )

            # Pricing
            st.subheader("Pris")
            pcol1, pcol2, pcol3 = st.columns(3)
            with pcol1:
                product["cost_price"] = st.number_input(
                    "Indkøbspris (EUR)",
                    value=float(product.get("cost_price") or 0),
                    key=f"cost_{i}",
                    step=1.0,
                    format="%.2f",
                )
            with pcol2:
                product["retail_price"] = st.number_input(
                    "Udsalgspris (DKK) ⚠️",
                    value=float(product.get("retail_price") or 0),
                    key=f"retail_{i}",
                    step=10.0,
                    format="%.2f",
                    help="PÅKRÆVET — sæt den rigtige udsalgspris",
                )
            with pcol3:
                product["compare_at_price"] = st.number_input(
                    "Før-pris (DKK, valgfri)",
                    value=float(product.get("compare_at_price") or 0),
                    key=f"compare_{i}",
                    step=10.0,
                    format="%.2f",
                )

            # Variants
            st.subheader("Varianter (størrelser)")
            variants = product.get("variants", [])
            variant_cols = st.columns(min(len(variants), 6) if variants else 1)
            for j, variant in enumerate(variants):
                col_idx = j % len(variant_cols)
                with variant_cols[col_idx]:
                    st.metric(
                        label=variant.get("size", "?"),
                        value=f"{variant.get('quantity', 0)} stk",
                    )

            # SEO
            with st.container():
                st.subheader("SEO")
                scol1, scol2 = st.columns(2)
                with scol1:
                    product["seo_title"] = st.text_input(
                        "SEO titel",
                        value=product.get("seo_title", ""),
                        key=f"seo_t_{i}",
                    )
                with scol2:
                    product["seo_description"] = st.text_input(
                        "Meta description",
                        value=product.get("seo_description", ""),
                        key=f"seo_d_{i}",
                    )

            # Images
            if product.get("image_urls"):
                st.subheader("Billeder")
                img_cols = st.columns(min(len(product["image_urls"]), 4))
                for j, url in enumerate(product["image_urls"]):
                    with img_cols[j % len(img_cols)]:
                        st.image(url, width=150)

            # Remove button
            if st.button(f"🗑️ Fjern produkt", key=f"remove_{i}"):
                products_to_remove.append(i)

    # Process removals
    if products_to_remove:
        for idx in sorted(products_to_remove, reverse=True):
            st.session_state.products.pop(idx)
        st.rerun()

    st.divider()

    # Validation
    missing_prices = [
        p for p in st.session_state.products if not p.get("retail_price")
    ]

    if missing_prices:
        st.warning(
            f"⚠️ {len(missing_prices)} produkt(er) mangler udsalgspris. Udfyld alle priser før push."
        )

    col_push1, col_push2 = st.columns([1, 3])
    with col_push1:
        push_disabled = len(missing_prices) > 0 or not SHOPIFY_ACCESS_TOKEN
        if st.button(
            "🚀 Push til Shopify", type="primary", disabled=push_disabled
        ):
            st.session_state.step = "pushing"
            st.rerun()
    with col_push2:
        if st.button("← Tilbage til upload"):
            st.session_state.step = "upload"
            st.session_state.products = []
            st.rerun()


# ════════════════════════════════════════════
# STEP 3: Push to Shopify
# ════════════════════════════════════════════
elif st.session_state.step == "pushing":
    st.header("3. Sender til Shopify...")

    shopify = ShopifyClient(SHOPIFY_STORE, SHOPIFY_ACCESS_TOKEN)
    progress_bar = st.progress(0)
    results = []

    for i, product in enumerate(st.session_state.products):
        progress = (i + 1) / len(st.session_state.products)
        progress_bar.progress(progress)

        with st.spinner(f"Opretter {product['title']}..."):
            try:
                # Parse tags back from comma string if edited
                if isinstance(product.get("tags"), str):
                    product["tags"] = [
                        t.strip() for t in product["tags"].split(",") if t.strip()
                    ]

                result = shopify.create_product(product)
                results.append(
                    {"product": product["title"], "status": "✅ Oprettet", "id": result}
                )
                st.success(f"✅ {product['title']} oprettet i Shopify")
            except Exception as e:
                results.append(
                    {"product": product["title"], "status": f"❌ Fejl: {str(e)}"}
                )
                st.error(f"❌ {product['title']}: {str(e)}")

    progress_bar.progress(1.0)
    st.session_state.push_results = results
    st.session_state.step = "done"
    st.rerun()


# ════════════════════════════════════════════
# STEP 4: Done
# ════════════════════════════════════════════
elif st.session_state.step == "done":
    st.header("4. Import fuldført!")
    st.balloons()

    for result in st.session_state.push_results:
        if "✅" in result["status"]:
            st.success(f"{result['status']} — {result['product']}")
        else:
            st.error(f"{result['status']} — {result['product']}")

    success_count = sum(
        1 for r in st.session_state.push_results if "✅" in r["status"]
    )
    total = len(st.session_state.push_results)
    st.metric("Resultat", f"{success_count}/{total} produkter oprettet")

    if st.button("📄 Importer flere fakturaer", type="primary"):
        st.session_state.products = []
        st.session_state.push_results = []
        st.session_state.step = "upload"
        st.rerun()
