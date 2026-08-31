"""
Image service — Brand website scraping + image filtering.

Bulletproof image pipeline with 6 strategies + Claude Vision verification.
Searches brand websites, multi-brand retailers, and Google Images (SerpAPI)
for product images, then verifies with Claude Vision that images match the product.
"""

import base64
import hashlib
import json
import logging
import re
import threading
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import anthropic
import httpx
from PIL import Image

from app.core.config import get_settings

# Maximum time (seconds) to spend on image search per product.
# After this, we skip remaining scraping strategies and jump to SerpAPI.
_MAX_SEARCH_TIME_SECONDS = 25

logger = logging.getLogger(__name__)

# Default headers for web scraping
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


# ═══════════════════════════════════════════════
# Product page scraping — material + description
# ═══════════════════════════════════════════════

def scrape_product_details(product_url: str, headers: dict | None = None) -> dict:
    """
    Scrape product details (material, description) from a product page.
    Returns dict with 'material', 'description_en' keys.
    """
    from bs4 import BeautifulSoup

    headers = headers or _DEFAULT_HEADERS
    details = {"material": "", "description_en": ""}

    try:
        response = httpx.get(product_url, headers=headers, timeout=10)
        if not response.is_success:
            return details

        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text(" ", strip=True).lower()

        # ── Extract material/composition ──
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
        logger.exception("Failed to scrape product details from %s", product_url)

    return details


# ═══════════════════════════════════════════════
# Claude Vision image filtering
# ═══════════════════════════════════════════════

def verify_and_filter_images_sync(
    image_urls: list[str],
    product_title: str,
    vendor: str,
    style_code: str,
    color: str = "",
    product_type: str = "",
    api_key: str | None = None,
) -> list[str]:
    """
    Use Claude Vision to VERIFY and FILTER scraped images (synchronous).

    Two-step check:
    1. IDENTITY — Is the image actually of THIS product (not a different item)?
    2. QUALITY — Is it a clean packshot (no models/people)?

    Returns only images that pass BOTH checks.
    If vision fails, returns EMPTY list — never unverified images.
    Sends small thumbnails (~512px) to minimize token cost.
    """
    if not image_urls:
        return []

    settings = get_settings()
    api_key = api_key or settings.anthropic_api_key
    if not api_key:
        logger.warning("[IMG-VERIFY] No API key — returning empty (cannot verify without key)")
        return []

    # Download images and build vision content
    image_contents = []
    url_map: dict[int, str] = {}  # 1-indexed

    for i, url in enumerate(image_urls):
        try:
            resp = httpx.get(url, headers=_DEFAULT_HEADERS, timeout=8)
            if not resp.is_success or len(resp.content) < 1000:
                continue

            # Resize to small thumbnail to minimize tokens
            try:
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                img.thumbnail((512, 512))
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=75)
                img_bytes = buf.getvalue()
            except Exception:
                img_bytes = resp.content

            # Skip tiny images that are likely placeholders
            if len(img_bytes) < 2000:
                continue

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
                "text": f"Image {i + 1}"
            })
            url_map[i + 1] = url
        except Exception:
            continue

    if not image_contents or not url_map:
        return []

    # Build product identity description for verification
    product_desc_parts = [f"Product: {product_title}"]
    if vendor:
        product_desc_parts.append(f"Brand: {vendor}")
    if style_code:
        product_desc_parts.append(f"SKU/Style: {style_code}")
    if color:
        product_desc_parts.append(f"Color: {color}")
    if product_type:
        product_desc_parts.append(f"Type: {product_type}")
    product_desc = "\n".join(product_desc_parts)

    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=60.0,  # 60s timeout for Vision verification calls
    )
    prompt_text = (
        f"{product_desc}\n\n"
        f"I have {len(url_map)} candidate images scraped from the web.\n"
        f"For EACH image, check these criteria:\n\n"
        f"1. IDENTITY — Is this the CORRECT product?\n"
        f"   - Must match the brand, product type, and color described above.\n"
        f"   - If the product type is specified (e.g., 'jacket'), the item MUST be that type.\n"
        f"     A shirt is NOT a jacket. Pants are NOT a sweater. Be precise about garment type.\n"
        f"   - Color should roughly match what is described (exact shade may vary).\n"
        f"   - When in doubt about identity, REJECT. False positives are worse than false negatives.\n\n"
        f"2. IMAGE TYPE — Is it a clean product image?\n"
        f"   ACCEPT any of these:\n"
        f"   - Packshot (product on white/plain/transparent background)\n"
        f"   - Flat lay (product laid flat, photographed from above)\n"
        f"   - Ghost mannequin / invisible mannequin\n"
        f"   - Product on hanger or display stand\n"
        f"   - Close-up / detail shot of the product\n"
        f"   - ANY product-only image regardless of background color\n\n"
        f"   REJECT any of these:\n"
        f"   - ANY image showing a person (even partially — hands, feet, torso)\n"
        f"   - Runway, editorial, campaign, or lifestyle shots\n"
        f"   - Catalog overview pages showing MULTIPLE different products\n"
        f"   - Size charts, logos, icons, or UI screenshots\n"
        f"   - Very low quality, blurry, or tiny placeholder images\n"
        f"   - Collages or multi-product comparison images\n\n"
        f"Be STRICT on identity (reject if unsure) but LENIENT on image type\n"
        f"(accept any clean product-only photo, regardless of background).\n\n"
        f"Reply with ONLY a JSON object:\n"
        f'{{ "approved": [1, 3], "rejected_reason": {{ "2": "wrong product type - shows pants not jacket" }} }}\n'
        f"If ALL pass: approved=[all indices], rejected_reason={{}}.\n"
        f"If NONE pass: approved=[], rejected_reason={{...for each}}."
    )

    image_contents.append({"type": "text", "text": prompt_text})

    # Retry logic: try up to 2 times on failure
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": image_contents}],
            )

            response_text = response.content[0].text.strip()

            # Try to parse full JSON response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                approved_indices = parsed.get("approved", [])
                rejected_reasons = parsed.get("rejected_reason", {})

                if rejected_reasons:
                    for idx_str, reason in rejected_reasons.items():
                        logger.info(f"[IMG-VERIFY] Rejected image {idx_str}: {reason}")

                filtered = [url_map[idx] for idx in approved_indices if idx in url_map]

                if filtered:
                    logger.info(
                        f"[IMG-VERIFY] '{product_title}': {len(filtered)}/{len(url_map)} images approved"
                    )
                    return filtered
                else:
                    logger.warning(
                        f"[IMG-VERIFY] '{product_title}': ALL {len(url_map)} images rejected — "
                        f"returning empty (reasons: {rejected_reasons})"
                    )
                    return []

            # Fallback: try to extract just an array
            array_match = re.search(r'\[[\d,\s]*\]', response_text)
            if array_match:
                approved_indices = json.loads(array_match.group())
                filtered = [url_map[idx] for idx in approved_indices if idx in url_map]
                if filtered:
                    return filtered

            # If we got a response but couldn't parse it, retry once
            if attempt == 0:
                logger.warning(f"[IMG-VERIFY] Unparseable response, retrying: {response_text[:200]}")
                continue
            break

        except Exception as e:
            if attempt == 0:
                logger.warning(f"[IMG-VERIFY] Attempt {attempt+1} failed for '{product_title}': {e}. Retrying...")
                time.sleep(1)
                continue
            logger.exception("[IMG-VERIFY] Vision verification failed for '%s' after 2 attempts", product_title)

    # Vision failed entirely — return empty list rather than risk wrong images
    logger.warning("[IMG-VERIFY] Vision failed — returning empty list (no unverified fallback)")
    return []


async def verify_and_filter_images(
    image_urls: list[str],
    product_title: str,
    vendor: str,
    style_code: str,
    color: str = "",
    product_type: str = "",
    api_key: str | None = None,
) -> list[str]:
    """Async wrapper — runs the sync vision verification in a thread."""
    import asyncio
    return await asyncio.to_thread(
        verify_and_filter_images_sync,
        image_urls, product_title, vendor, style_code, color, product_type, api_key,
    )


# Keep legacy name as alias
filter_images_with_vision = verify_and_filter_images


# ═══════════════════════════════════════════════
# Brand website search — find product page + images
# ═══════════════════════════════════════════════

def _get_brand_search_config(vendor: str, sku_encoded: str, style_code: str) -> tuple[str, str]:
    """
    Return (brand_search_url, brand_domain) for the given vendor.
    Contains ALL brand-specific URL patterns.
    """
    vendor_lower = vendor.lower()

    if "american vintage" in vendor_lower:
        return (f"https://www.americanvintage-store.com/en/search?q={sku_encoded}", "americanvintage-store.com")

    # Specific "comme des" sub-brands BEFORE generic CDG catch-all
    if "comme des" in vendor_lower and "wallet" in vendor_lower:
        return (f"https://shop.doverstreetmarket.com/search?q={sku_encoded}", "doverstreetmarket.com")
    if "comme des" in vendor_lower and "parfum" in vendor_lower:
        return (f"https://shop.doverstreetmarket.com/search?q={sku_encoded}", "doverstreetmarket.com")
    if "comme des" in vendor_lower or "cdg" in vendor_lower:
        return (f"https://shop.doverstreetmarket.com/search?q={sku_encoded}", "doverstreetmarket.com")

    if "acne" in vendor_lower:
        return (f"https://www.acnestudios.com/dk/en/search?q={sku_encoded}", "acnestudios.com")
    if "norse projects" in vendor_lower:
        return (f"https://www.norseprojects.com/search?q={sku_encoded}", "norseprojects.com")
    if "our legacy" in vendor_lower:
        return (f"https://www.ourlegacy.com/search?q={sku_encoded}", "ourlegacy.com")
    if "maison margiela" in vendor_lower or "margiela" in vendor_lower or "mm6" in vendor_lower:
        return (f"https://www.maisonmargiela.com/search?q={sku_encoded}", "maisonmargiela.com")

    if "a.p.c" in vendor_lower or "apc" in vendor_lower:
        # A.P.C. SKU format: FARVEKODE-MODELNUMMER (e.g., "COHBU-M26388")
        # The model number (M26388) is the searchable part, not the color prefix
        apc_model = style_code
        if "-" in style_code:
            parts = style_code.split("-")
            for part in parts:
                if len(part) >= 4 and part[0].isalpha() and any(c.isdigit() for c in part[1:]):
                    apc_model = part
                    break
        apc_encoded = quote(apc_model)
        return (f"https://www.apc.fr/en/search?q={apc_encoded}", "apc.fr")

    if "carhartt" in vendor_lower:
        return (f"https://www.carhartt-wip.com/en/search?q={sku_encoded}", "carhartt-wip.com")
    if "modstr" in vendor_lower or "modström" in vendor_lower:
        return (f"https://www.modstrom.com/search?q={sku_encoded}", "modstrom.com")
    if "sunflower" in vendor_lower:
        return (f"https://hellosunflower.com/search?q={sku_encoded}", "hellosunflower.com")
    if "salomon" in vendor_lower:
        return (f"https://www.salomon.com/en-dk/search?q={sku_encoded}", "salomon.com")
    if "new balance" in vendor_lower:
        return (f"https://www.newbalance.dk/search?q={sku_encoded}", "newbalance.dk")
    if "birkenstock" in vendor_lower:
        return (f"https://www.birkenstock.com/dk/search?q={sku_encoded}", "birkenstock.com")
    if "service works" in vendor_lower:
        return (f"https://serviceworks.co.uk/search?q={sku_encoded}", "serviceworks.co.uk")
    if "alohas" in vendor_lower:
        return (f"https://www.alohas.io/search?q={sku_encoded}", "alohas.io")
    if "marni" in vendor_lower:
        return (f"https://www.marni.com/search?q={sku_encoded}", "marni.com")
    if "mizuno" in vendor_lower:
        return (f"https://www.mizuno.com/search?q={sku_encoded}", "mizuno.com")
    if "timberland" in vendor_lower:
        return (f"https://www.timberland.dk/search?q={sku_encoded}", "timberland.dk")
    if "66" in vendor_lower and "north" in vendor_lower:
        return (f"https://www.66north.com/search?q={sku_encoded}", "66north.com")
    if "toteme" in vendor_lower:
        return (f"https://toteme-studio.com/search?q={sku_encoded}", "toteme-studio.com")
    if "parel" in vendor_lower:
        return (f"https://parelstudios.com/search?q={sku_encoded}", "parelstudios.com")
    if "hestra" in vendor_lower:
        return (f"https://www.hestragloves.com/search?q={sku_encoded}", "hestragloves.com")
    if "oamc" in vendor_lower:
        return (f"https://www.oamc.com/search?q={sku_encoded}", "oamc.com")
    if "sophie bille" in vendor_lower:
        return (f"https://sophiebillebrahe.com/search?q={sku_encoded}", "sophiebillebrahe.com")
    if "sofie ladefoged" in vendor_lower:
        return (f"https://sofieladefoged.com/search?q={sku_encoded}", "sofieladefoged.com")
    if "dragon diffusion" in vendor_lower:
        return (f"https://www.dragondiffusion.com/search?q={sku_encoded}", "dragondiffusion.com")
    if "berner" in vendor_lower:
        return (f"https://bernerkuhl.com/search?q={sku_encoded}", "bernerkuhl.com")
    if "gabi" in vendor_lower:
        return (f"https://www.gabigamel.com/search?q={sku_encoded}", "gabigamel.com")
    if "fichi" in vendor_lower:
        return (f"https://fichi.dk/search?q={sku_encoded}", "fichi.dk")
    if "flowerism" in vendor_lower:
        return (f"https://flowerismstudio.com/search?q={sku_encoded}", "flowerismstudio.com")
    if "flatlist" in vendor_lower:
        return (f"https://flatlisteyewear.com/search?q={sku_encoded}", "flatlisteyewear.com")
    if "monokel" in vendor_lower:
        return (f"https://monokeleyewear.com/search?q={sku_encoded}", "monokeleyewear.com")

    return ("", "")


def _build_image_bank_search_pattern(base_url: str, bank_type: str) -> str:
    """
    Auto-construct image bank search URL pattern from a base URL and portal type.

    Known portal types and their typical search URL structures:
    - brandos: Brandos B2B image portal — /search?q={sku}
    - datadwell: Datadwell DAM — /search?q={sku}
    - canto: Canto DAM — /#/search/{sku}
    - trendmark: Trendmark PIM — /search?query={sku}
    - custom: No auto-construction, user must provide full pattern

    Returns empty string if pattern can't be auto-constructed.
    """
    if not base_url or not bank_type or bank_type == "custom":
        return ""

    # Normalize base URL: ensure https://, strip trailing slash
    url = base_url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    url = url.rstrip("/")

    patterns = {
        "brandos": f"{url}/search?q={{sku}}",
        "datadwell": f"{url}/search?q={{sku}}",
        "canto": f"{url}/search?keyword={{sku}}",
        "trendmark": f"{url}/search?query={{sku}}",
    }

    return patterns.get(bank_type, "")


# Cache for brand domain guessing — avoids repeated HEAD requests per vendor
_brand_domain_cache: dict[str, list[str]] = {}
_brand_domain_cache_lock = threading.Lock()


def _guess_brand_domains(vendor: str) -> list[str]:
    """
    Guess the most likely website domains for a brand based on its name.
    Caches results to avoid repeated network checks for the same vendor.
    Returns up to 2 working domains.
    """
    vendor_clean = vendor.strip()
    if not vendor_clean or len(vendor_clean) < 2:
        return []

    cache_key = vendor_clean.lower()
    with _brand_domain_cache_lock:
        if cache_key in _brand_domain_cache:
            return _brand_domain_cache[cache_key]

    # Normalize: lowercase, remove special chars, collapse spaces
    slug = re.sub(r'[^a-z0-9\s]', '', cache_key).strip()
    slug_no_spaces = slug.replace(" ", "")
    slug_dashed = slug.replace(" ", "-")

    candidates = []
    if " " in slug:
        candidates.append(f"www.{slug_dashed}.com")
        candidates.append(f"{slug_dashed}.com")
    else:
        candidates.append(f"www.{slug_no_spaces}.com")
        candidates.append(f"{slug_no_spaces}.com")
    candidates.append(f"{slug_dashed}.dk")

    # Quick HEAD check: only return domains that actually resolve
    valid = []
    for domain in candidates:
        try:
            resp = httpx.head(
                f"https://{domain}", headers=_DEFAULT_HEADERS,
                timeout=2, follow_redirects=True,
            )
            if resp.is_success:
                valid.append(domain)
                if len(valid) >= 2:
                    break
        except Exception:
            continue

    with _brand_domain_cache_lock:
        _brand_domain_cache[cache_key] = valid
    if valid:
        logger.info(f"[IMG] Discovered brand site for '{vendor}': {valid}")
    return valid


def _search_google_site_specific(
    brand_domain: str,
    style_code: str,
    vendor: str,
    title: str,
    headers: dict,
    max_images: int = 5,
) -> dict:
    """
    Search Google for a product on a SPECIFIC brand domain via SerpAPI web search.

    Uses query: site:brand-domain.com "SKU" to find the exact product page.
    Then scrapes images from that page. This is the most reliable strategy for
    JS-rendered brand sites because Google has already crawled and rendered them.

    Returns dict with 'images' (list of URLs) and 'product_page_url' (str).
    """
    settings = get_settings()
    if not settings.serpapi_key:
        return {"images": [], "product_page_url": ""}

    if not brand_domain or not style_code:
        return {"images": [], "product_page_url": ""}

    # Clean domain (remove https://, www., trailing /)
    domain_clean = brand_domain.lower().strip()
    domain_clean = domain_clean.replace("https://", "").replace("http://", "")
    domain_clean = domain_clean.replace("www.", "").rstrip("/")

    # Build queries — most specific first
    # A.P.C. special: extract model number from composite SKU
    sku_search = style_code
    vendor_lower = vendor.lower()
    if "a.p.c" in vendor_lower or "apc" in vendor_lower:
        if "-" in style_code:
            parts = style_code.split("-")
            for part in parts:
                if len(part) >= 4 and part[0].isalpha() and any(c.isdigit() for c in part[1:]):
                    sku_search = part
                    break

    queries = [
        f'site:{domain_clean} "{sku_search}"',  # Exact SKU match on brand domain
        f'site:{domain_clean} {sku_search}',     # Looser SKU match
    ]
    # Also try with product title if different from SKU
    if title and title.lower().strip() != style_code.lower().strip():
        queries.append(f'site:{domain_clean} "{title}"')

    # Also try with clean title keywords (not full title, just key words)
    if title:
        # Extract meaningful words (skip common filler words)
        filler = {"the", "a", "an", "in", "on", "for", "with", "and", "or", "de", "la", "le", "du", "des", "en"}
        title_words = [w for w in title.lower().split() if w not in filler and len(w) > 2]
        if len(title_words) > 1:
            title_query = " ".join(title_words[:4])  # Max 4 keywords
            queries.append(f'site:{domain_clean} {title_query}')

    for query in queries:
        try:
            resp = httpx.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google",
                    "q": query,
                    "api_key": settings.serpapi_key,
                    "num": 5,
                },
                timeout=10,
            )
            if not resp.is_success:
                logger.debug(f"[IMG] SerpAPI site-search returned {resp.status_code} for '{query}'")
                continue

            data = resp.json()
            organic = data.get("organic_results", [])

            if not organic:
                continue

            # Find the best product page URL from results
            best_url = ""
            best_score = 0

            for result in organic:
                url = result.get("link", "")
                snippet = (result.get("snippet", "") or "").lower()
                result_title = (result.get("title", "") or "").lower()
                url_lower = url.lower()

                if not url or domain_clean not in url_lower:
                    continue

                score = 0

                # Product page indicators
                if any(seg in url_lower for seg in ["/product/", "/products/", "/p/", "/shop/", "/item/"]):
                    score += 40

                # SKU in URL
                sku_lower = sku_search.lower()
                if sku_lower in url_lower.replace("-", "").replace("_", ""):
                    score += 30

                # SKU in snippet or title
                if sku_lower in snippet or sku_lower in result_title:
                    score += 20

                # Avoid category/collection/blog pages
                if any(skip in url_lower for skip in ["/category/", "/collection/", "/blog/", "/news/", "/about/"]):
                    score -= 50

                if score > best_score:
                    best_score = score
                    best_url = url

            if best_url and best_score > 0:
                logger.info(f"[IMG] Google site-search found product page: {best_url} (score={best_score})")

                # Scrape images from the found product page
                sku_lower = style_code.lower().strip()
                imgs = _get_all_images_from_product_page(best_url, sku_lower, headers, max_images)
                if imgs:
                    return {"images": imgs, "product_page_url": best_url}
                else:
                    logger.debug(f"[IMG] Found product page but no images: {best_url}")

        except Exception as e:
            logger.warning(f"[IMG] SerpAPI site-search failed for '{query}': {e}")
            continue

    return {"images": [], "product_page_url": ""}


def _search_google_images(
    vendor: str,
    style_code: str,
    title: str,
    max_images: int = 5,
) -> list[str]:
    """
    Search Google Images via SerpAPI for product packshots.

    Uses a targeted query: '"brand" "SKU" product' to find product-specific images.
    Filters results to only include likely product images (correct size, no icons).

    Returns list of image URLs or empty list if SerpAPI key is not configured.
    """
    settings = get_settings()
    if not settings.serpapi_key:
        return []

    # Build targeted search queries — multiple attempts from specific to broad.
    # Clean vendor name for Google (remove dots, normalize spacing)
    vendor_clean = re.sub(r'\.', '', vendor).strip()  # "A.P.C." → "APC"

    # Short numeric SKUs (e.g. "1318", "5171") are too generic for Google Images.
    # For these, prioritize title-based queries over SKU-based ones.
    _is_short_numeric_sku = len(style_code) <= 5 and style_code.isdigit()

    queries_to_try = []
    if _is_short_numeric_sku and title and title.lower().strip() != style_code.lower().strip():
        # Title-first for short numeric SKUs
        queries_to_try.append(f'{vendor_clean} "{title}" product packshot')
        queries_to_try.append(f'{vendor_clean} "{title}" product')
        queries_to_try.append(f"{vendor_clean} {title}")
        # Still try SKU as last resort
        queries_to_try.append(f'{vendor_clean} "{style_code}" product')
    else:
        if style_code:
            # Most specific: brand + exact SKU + product-type qualifier
            queries_to_try.append(f'{vendor_clean} "{style_code}" product packshot')
            # Without packshot qualifier
            queries_to_try.append(f'{vendor_clean} "{style_code}" product')
            # Without quotes (some sites don't have exact SKU in indexable text)
            queries_to_try.append(f"{vendor_clean} {style_code}")
        if title and title.lower().strip() != style_code.lower().strip():
            # Brand + product title + "product" to bias towards packshots
            queries_to_try.append(f'{vendor_clean} "{title}" product')
    if not queries_to_try:
        return []

    for query in queries_to_try:
        try:
            resp = httpx.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_images",
                    "q": query,
                    "api_key": settings.serpapi_key,
                    "num": 15,  # 15 is enough after filtering, saves API credits
                    "safe": "active",
                    "ijn": "0",  # First page
                },
                timeout=10,
            )
            if not resp.is_success:
                logger.warning(f"[IMG] SerpAPI returned {resp.status_code} for query '{query}'")
                continue

            data = resp.json()
            images_results = data.get("images_results", [])

            if not images_results:
                continue

            # Filter and score results
            candidates: list[tuple[int, str]] = []
            vendor_slug = vendor.lower().replace(" ", "")
            vendor_words = [w.lower() for w in vendor.split() if len(w) > 2]
            other_brands = ["nike", "adidas", "gucci", "prada", "balenciaga", "chanel",
                            "louis vuitton", "dior", "versace", "fendi", "burberry"]
            for img in images_results:
                url = img.get("original", "")
                thumbnail = img.get("thumbnail", "")
                img_title = (img.get("title", "") or "").lower()
                source = (img.get("source", "") or "").lower()
                width = img.get("original_width", 0) or 0
                height = img.get("original_height", 0) or 0

                if not url:
                    continue

                # Skip tiny images (logos, icons)
                if width and height and (width < 300 or height < 300):
                    continue

                # Skip extremely wide/tall images (likely banners or UI elements)
                if width and height:
                    ratio = max(width, height) / max(min(width, height), 1)
                    if ratio > 3.0:
                        continue

                # Skip non-image URLs
                url_lower = url.lower()
                if not any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp", "image"]):
                    # Allow CDN URLs that don't have extensions
                    if not any(cdn in url_lower for cdn in ["cdn.", "cloudfront", "shopify", "imgix"]):
                        continue

                # Skip obvious non-product images
                if any(skip in url_lower for skip in [
                    "logo", "icon", "favicon", "banner", "social", "avatar",
                    "sprite", "pixel", "tracking", "badge",
                    "header", "footer", "newsletter", "signup",
                ]):
                    continue

                # Skip stock photo domains
                if any(spd in url_lower for spd in [
                    "shutterstock.com", "istockphoto.com", "gettyimages.com",
                    "unsplash.com", "pexels.com",
                ]):
                    continue

                # Hard-reject obvious model/lifestyle from title (titles are descriptive, safer to filter on)
                model_title_kw = ["lookbook", "editorial", "runway", "campaign", "lifestyle",
                                  "street style", "outfit", "wearing", "worn by"]
                if any(kw in img_title for kw in model_title_kw):
                    continue

                # Score: prefer images from brand sites and fashion retailers
                score = 0

                # Brand's own site — highest trust
                if vendor_slug in source or vendor_slug in url_lower:
                    score += 50

                # Known fashion retailers
                trusted_sources = [
                    "ssense", "farfetch", "endclothing", "mrporter",
                    "mytheresa", "luisaviaroma", "nordstrom", "shopify",
                    "24s.com", "antonioli", "ln-cc",
                ]
                if any(ts in source or ts in url_lower for ts in trusted_sources):
                    score += 30

                # SKU in URL or title
                if style_code and style_code.lower() in (url_lower + " " + img_title):
                    score += 20

                # Prefer larger images
                if width >= 800:
                    score += 10
                elif width >= 400:
                    score += 5

                # Packshot keywords in URL
                packshot_kw = ["product", "packshot", "flat", "front", "detail"]
                if any(kw in url_lower for kw in packshot_kw):
                    score += 10

                # Negative signals: likely wrong product
                # If the image title mentions a completely different brand, skip it
                if vendor_words:
                    title_has_brand = any(w in img_title for w in vendor_words)
                    # If the title mentions another well-known brand, it's likely wrong
                    title_has_other_brand = any(b in img_title for b in other_brands if b not in vendor.lower())
                    if title_has_other_brand and not title_has_brand:
                        continue  # Skip - clearly a different brand's product

                candidates.append((score, url))

            if not candidates:
                continue

            # Sort by score descending, take top results
            candidates.sort(key=lambda x: x[0], reverse=True)
            found_urls = [url for _, url in candidates[:max_images]]

            if found_urls:
                logger.info(
                    f"[IMG] Google Images: {len(found_urls)} candidates from query '{query}' "
                    f"(top score: {candidates[0][0]})"
                )
                return found_urls

        except Exception as e:
            logger.warning(f"[IMG] SerpAPI search failed for '{query}': {e}")
            continue

    return []


# ═══════════════════════════════════════════════
# Brand Drive folder — authenticated image source
# ═══════════════════════════════════════════════

# Drive image files we can hand on to Shopify.
_DRIVE_IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp")


def _drive_sku_variants(style_code: str) -> list[str]:
    """
    The forms a packshot filename might spell the SKU in.

    Brands file images as "COHBU-M26388_1.jpg" but also as "COHBUM26388.jpg",
    so search for the code with and without its separators.
    """
    code = (style_code or "").strip()
    if not code:
        return []
    variants = [code]
    stripped = re.sub(r"[\s\-_./]", "", code)
    if stripped and stripped != code:
        variants.append(stripped)
    return variants


def fetch_brand_drive_images(
    folder_id: str,
    access_token: str,
    style_code: str,
    vendor: str,
    max_images: int = 5,
) -> list[str]:
    """
    Fetch a product's packshots from the brand's Drive folder.

    Drive files are not publicly readable, and Shopify fetches images by URL, so
    the bytes are downloaded and written into the app's own image directory and
    returned as public URLs — the same route that serves manually uploaded
    images.

    Synchronous on purpose: the whole image search runs in a worker thread, and
    googleapiclient is synchronous anyway.

    Returns an empty list when the folder holds nothing for this SKU.
    """
    from app.services.drive_service import build_drive_client

    from app.services.drive_service import normalise_folder_id

    settings = get_settings()
    variants = _drive_sku_variants(style_code)
    # A brand's drive_folder_id is pasted by hand and is usually the folder URL.
    folder_id = normalise_folder_id(folder_id)
    if not variants or not folder_id or not access_token:
        return []

    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    name_clause = " or ".join(f"name contains '{_escape(v)}'" for v in variants)
    mime_clause = " or ".join(f"mimeType = '{m}'" for m in _DRIVE_IMAGE_MIME_TYPES)
    query = (
        f"'{_escape(folder_id)}' in parents and trashed = false "
        f"and ({mime_clause}) and ({name_clause})"
    )

    client = build_drive_client(access_token)
    response = client.files().list(
        q=query,
        pageSize=max_images,
        fields="files(id, name, mimeType)",
        orderBy="name",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    files = (response.get("files") or [])[:max_images]
    if not files:
        return []

    # Mirror the layout the upload route uses, keyed by SKU so a re-run
    # overwrites rather than accumulating copies.
    safe_vendor = re.sub(r"[^A-Za-z0-9._-]", "_", vendor or "unknown")[:64]
    safe_sku = re.sub(r"[^A-Za-z0-9._-]", "_", style_code)[:64]
    relative_dir = Path("drive") / safe_vendor / safe_sku
    target_dir = Path(settings.image_upload_dir) / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o750)

    base_url = settings.public_base_url.rstrip("/")
    urls: list[str] = []

    for item in files:
        try:
            data = client.files().get_media(fileId=item["id"]).execute()
        except Exception as e:
            logger.warning(f"[IMG] Could not download Drive image {item.get('name')}: {e}")
            continue
        if not data:
            continue

        # Separators become underscores, so a name like "../../x.jpg" cannot
        # walk out of the target directory. Leading dots go too, so a Drive file
        # cannot land as a hidden file.
        safe_name = (
            re.sub(r"[^A-Za-z0-9._-]", "_", item.get("name", "image"))[:128].lstrip(".")
            or "image"
        )
        (target_dir / safe_name).write_bytes(data)
        urls.append(f"{base_url}/api/v1/images/{relative_dir.as_posix()}/{safe_name}")

    return urls


def find_product_images_and_details(
    vendor: str,
    style_code: str,
    title: str,
    max_images: int = 5,
    brand_config: dict | None = None,
) -> dict:
    """
    Search for product images AND scrape product details (material, description)
    from brand websites. Uses SKU to find the actual product page, then extracts everything.

    Args:
        vendor: Brand name
        style_code: SKU / article code
        title: Product title
        max_images: Max images to return
        brand_config: Optional dict from DB Brand record with:
            - website_url: Brand's website URL
            - search_url_pattern: URL pattern with {sku} placeholder

    Returns dict with:
      - 'images': list of URLs
      - 'details': {material, description_en}
      - 'image_source': which strategy found the images (for debugging)
      - 'product_page_url': the URL of the matched product page (for verification)
    """
    result = {
        "images": [],
        "details": {"material": "", "description_en": ""},
        "image_source": "none",
        "product_page_url": "",
    }

    if not style_code:
        return result

    headers = _DEFAULT_HEADERS.copy()
    vendor_lower = vendor.lower()
    sku_lower = style_code.lower().strip()
    sku_encoded = quote(style_code)
    _search_start = time.monotonic()

    def _time_left() -> bool:
        """Check if we still have time budget for scraping strategies."""
        return (time.monotonic() - _search_start) < _MAX_SEARCH_TIME_SECONDS

    def _serpapi_fallback(res: dict, v: str, sku: str, t: str, mx: int) -> dict | None:
        """Quick SerpAPI fallback when time budget is exceeded."""
        imgs = _search_google_images(v, sku, t, mx)
        if imgs:
            validated = _validate_image_urls(imgs)
            if validated:
                res["images"] = validated
                res["image_source"] = "google_images_fast"
                return res
        return None

    # ── Strategy IB: Image bank portal search (highest priority) ──
    # Image banks (Brandos, Datadwell, Canto, Trendmark) contain curated packshot
    # images — they're the best quality source. Try these FIRST.
    ib_search_pattern = (brand_config or {}).get("image_bank_search_pattern", "") or ""
    ib_url = (brand_config or {}).get("image_bank_url", "") or ""
    ib_type = (brand_config or {}).get("image_bank_type", "") or ""

    # If we have a search pattern, use it directly
    # If we only have a base URL + known type, auto-construct the search pattern
    if not ib_search_pattern and ib_url and ib_type:
        ib_search_pattern = _build_image_bank_search_pattern(ib_url, ib_type)

    if ib_search_pattern and "{sku}" in ib_search_pattern:
        ib_search_url = ib_search_pattern.replace("{sku}", sku_encoded)
        try:
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(ib_search_url)
            ib_domain = parsed.netloc.replace("www.", "")
        except Exception:
            ib_domain = ""

        if ib_domain:
            logger.info(f"[IMG] Trying image bank: {ib_domain} for SKU '{style_code}'")
            product_page_url = _find_product_page_from_search(ib_search_url, sku_lower, ib_domain, headers)
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
                if imgs:
                    result["images"] = imgs
                    result["image_source"] = f"image_bank:{ib_domain}"
                    result["product_page_url"] = product_page_url
                    # Image bank pages may also have product details
                    result["details"] = scrape_product_details(product_page_url, headers)
                    logger.info(
                        f"[IMG] Image bank hit: {len(imgs)} images from {ib_domain} "
                        f"for SKU '{style_code}'"
                    )
                    return result
                else:
                    logger.debug(f"[IMG] Image bank page found but no images: {product_page_url}")
            else:
                # Image bank search didn't find a product page — try Google site-search
                # on the image bank domain (many DAMs are JS-rendered)
                if _time_left():
                    site_result = _search_google_site_specific(
                        brand_domain=ib_domain,
                        style_code=style_code,
                        vendor=vendor,
                        title=title,
                        headers=headers,
                        max_images=max_images,
                    )
                    if site_result["images"]:
                        result["images"] = site_result["images"]
                        result["image_source"] = f"google_site_ib:{ib_domain}"
                        result["product_page_url"] = site_result["product_page_url"]
                        if site_result["product_page_url"]:
                            result["details"] = scrape_product_details(
                                site_result["product_page_url"], headers
                            )
                        logger.info(
                            f"[IMG] Google site-search on image bank hit: "
                            f"{len(result['images'])} images from {ib_domain}"
                        )
                        return result

    # Resolve brand domain from DB config or hardcoded config
    db_website_url = (brand_config or {}).get("website_url", "") or ""
    db_search_pattern = (brand_config or {}).get("search_url_pattern", "") or ""

    # ── Strategy D: Brand's Google Drive folder ──
    # An authenticated, curated source: the packshots the brand actually sent
    # us. Tried before any SerpAPI strategy — it costs nothing, needs no
    # scraping, and the files are already the right product.
    drive_folder_id = (brand_config or {}).get("drive_folder_id", "") or ""
    drive_access_token = (brand_config or {}).get("drive_access_token", "") or ""
    if drive_folder_id and drive_access_token:
        try:
            drive_images = fetch_brand_drive_images(
                folder_id=drive_folder_id,
                access_token=drive_access_token,
                style_code=style_code,
                vendor=vendor,
                max_images=max_images,
            )
            if drive_images:
                result["images"] = drive_images
                result["image_source"] = "brand_drive"
                logger.info(
                    f"[IMG] Brand Drive folder hit: {len(drive_images)} image(s) "
                    f"for SKU '{style_code}'"
                )
                return result
        except Exception as e:
            # Never let Drive take down the whole image search — the scraping
            # and SerpAPI strategies below are still worth trying.
            logger.warning(f"[IMG] Brand Drive lookup failed for '{style_code}': {e}")

    # Extract domain from website_url for Google site-specific search
    brand_domain_for_google = ""
    if db_website_url:
        try:
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(db_website_url)
            brand_domain_for_google = parsed.netloc or parsed.path.split("/")[0]
            brand_domain_for_google = brand_domain_for_google.replace("www.", "")
        except Exception:
            pass

    # ── Strategy 0: DB-configured brand search URL pattern ──
    # Uses the search_url_pattern stored in the Brand record (e.g. https://66north.com/search?q={sku})
    if db_search_pattern and "{sku}" in db_search_pattern:
        db_search_url = db_search_pattern.replace("{sku}", sku_encoded)
        # Extract domain from the search URL for link matching
        try:
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(db_search_url)
            db_domain = parsed.netloc.replace("www.", "")
        except Exception:
            db_domain = ""

        if db_domain:
            product_page_url = _find_product_page_from_search(db_search_url, sku_lower, db_domain, headers)
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
                if imgs:
                    result["images"] = imgs
                    result["image_source"] = f"db_brand:{db_domain}"
                    result["product_page_url"] = product_page_url
                result["details"] = scrape_product_details(product_page_url, headers)
                if result["images"]:
                    logger.info(f"[IMG] DB brand config hit: {len(imgs)} images from {db_domain} for SKU '{style_code}'")
                    return result

    # ── Strategy 0b: Google site-specific search on brand domain ──
    # When the brand site is JS-rendered and direct search fails, Google has already
    # crawled and rendered it. Query: site:66north.com "SKU" finds the exact product page.
    if brand_domain_for_google and _time_left():
        site_result = _search_google_site_specific(
            brand_domain=brand_domain_for_google,
            style_code=style_code,
            vendor=vendor,
            title=title,
            headers=headers,
            max_images=max_images,
        )
        if site_result["images"]:
            result["images"] = site_result["images"]
            result["image_source"] = f"google_site:{brand_domain_for_google}"
            result["product_page_url"] = site_result["product_page_url"]
            if site_result["product_page_url"]:
                result["details"] = scrape_product_details(site_result["product_page_url"], headers)
            logger.info(
                f"[IMG] Google site-search hit: {len(result['images'])} images "
                f"from {brand_domain_for_google} for SKU '{style_code}'"
            )
            return result

    # ── Strategy 1: Brand website — find product page by SKU, then get images + details ──
    # Uses hardcoded brand search configs as fallback for brands not yet in DB
    brand_search_url, brand_domain = _get_brand_search_config(vendor, sku_encoded, style_code)

    # If no hardcoded config but we have a DB website_url, build a generic search URL
    if not brand_search_url and db_website_url:
        brand_search_url = f"{db_website_url.rstrip('/')}/search?q={sku_encoded}"
        brand_domain = brand_domain_for_google or ""

    if brand_search_url:
        product_page_url = _find_product_page_from_search(brand_search_url, sku_lower, brand_domain, headers)
        if product_page_url:
            imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
            if imgs:
                result["images"] = imgs
                result["image_source"] = f"brand:{brand_domain}"
                result["product_page_url"] = product_page_url
            result["details"] = scrape_product_details(product_page_url, headers)
            if result["images"]:
                logger.info(f"[IMG] Brand site hit: {len(imgs)} images from {brand_domain} for SKU '{style_code}'")
                return result

        # ── Strategy 1 fallback: Search by product title instead of SKU ──
        # Short numeric SKUs (e.g. "1318", "5171") are too generic for search.
        # Try searching with the product title (e.g. "new base shirt") on the brand site.
        _is_short_numeric_sku = len(style_code) <= 5 and style_code.isdigit()
        if not result["images"] and title and _time_left() and _is_short_numeric_sku:
            title_encoded = quote(title)
            title_search_url = brand_search_url.split("?")[0] + f"?q={title_encoded}"
            title_lower = title.lower().strip()
            logger.info(f"[IMG] SKU '{style_code}' is short numeric — retrying brand search with title '{title}'")
            product_page_url = _find_product_page_from_search(
                title_search_url, title_lower, brand_domain, headers
            )
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, title_lower, headers, max_images)
                if imgs:
                    result["images"] = imgs
                    result["image_source"] = f"brand_title:{brand_domain}"
                    result["product_page_url"] = product_page_url
                result["details"] = scrape_product_details(product_page_url, headers)
                if result["images"]:
                    logger.info(f"[IMG] Brand title-search hit: {len(imgs)} images from {brand_domain} for '{title}'")
                    return result

    # ── Strategy 1b: Google site-search for hardcoded brand domain ──
    # If Strategy 1 failed (JS-rendered search page) but we know the brand domain,
    # try Google site-specific search. Skip if Strategy 0b already tried this domain.
    if brand_domain and brand_domain != brand_domain_for_google and _time_left():
        site_result = _search_google_site_specific(
            brand_domain=brand_domain,
            style_code=style_code,
            vendor=vendor,
            title=title,
            headers=headers,
            max_images=max_images,
        )
        if site_result["images"]:
            result["images"] = site_result["images"]
            result["image_source"] = f"google_site:{brand_domain}"
            result["product_page_url"] = site_result["product_page_url"]
            if site_result["product_page_url"]:
                result["details"] = scrape_product_details(site_result["product_page_url"], headers)
            logger.info(
                f"[IMG] Google site-search (hardcoded) hit: {len(result['images'])} images "
                f"from {brand_domain} for SKU '{style_code}'"
            )
            return result

    # ── Strategy 2: Multi-brand retailers — expanded list ──
    if not _time_left():
        logger.info(f"[IMG] Time budget exceeded, jumping to SerpAPI for '{title}'")
        return _serpapi_fallback(result, vendor, style_code, title, max_images) or result

    # For short numeric SKUs (e.g. "1318"), searching by SKU alone is too generic
    # on multi-brand retailers. Use "vendor + title" instead for a better match.
    _is_short_numeric_sku = len(style_code) <= 5 and style_code.isdigit()
    if _is_short_numeric_sku and title:
        retailer_query = quote(f"{vendor} {title}")
        retailer_match_term = title.lower().strip()
    else:
        retailer_query = sku_encoded
        retailer_match_term = sku_lower

    retailer_searches = [
        (f"https://www.ssense.com/en-dk/search?q={retailer_query}", "ssense.com"),
        (f"https://www.farfetch.com/dk/shopping/search/items/?q={retailer_query}", "farfetch.com"),
        (f"https://www.endclothing.com/dk/catalogsearch/result/?q={retailer_query}", "endclothing.com"),
        (f"https://www.mrporter.com/en-dk/search?q={retailer_query}", "mrporter.com"),
        (f"https://www.mytheresa.com/search?q={retailer_query}", "mytheresa.com"),
        (f"https://www.luisaviaroma.com/en-dk/search?q={retailer_query}", "luisaviaroma.com"),
        (f"https://www.24s.com/en-dk/search?q={retailer_query}", "24s.com"),
        (f"https://www.antonioli.eu/en/search?q={retailer_query}", "antonioli.eu"),
        (f"https://www.ln-cc.com/en/search?q={retailer_query}", "ln-cc.com"),
    ]

    for search_url, domain in retailer_searches:
        product_page_url = _find_product_page_from_search(search_url, retailer_match_term, domain, headers)
        if product_page_url:
            imgs = _get_all_images_from_product_page(product_page_url, retailer_match_term, headers, max_images)
            if imgs:
                result["images"] = imgs
                result["image_source"] = f"retailer:{domain}"
                result["product_page_url"] = product_page_url
                if not result["details"]["material"]:
                    result["details"] = scrape_product_details(product_page_url, headers)
                logger.info(f"[IMG] Retailer hit: {len(imgs)} images from {domain} for SKU '{style_code}'")
                return result

    # ── Strategy 3: Try alternative SKU formats ──
    if not _time_left():
        logger.info(f"[IMG] Time budget exceeded after Strategy 2, jumping to SerpAPI for '{title}'")
        fb = _serpapi_fallback(result, vendor, style_code, title, max_images)
        return fb if fb else result

    # Use the generic composite SKU splitter for ALL brands
    alt_skus = set(_generate_sku_variants(style_code.lower()))
    alt_skus.discard(style_code.lower())

    # Also add dot-stripped version
    alt_skus.add(style_code.replace(".", "").lower())
    # A.P.C. specific: extract model part with alpha+digit pattern
    if "a.p.c" in vendor_lower or "apc" in vendor_lower:
        for part in style_code.split("-"):
            if part and part[0].isalpha() and any(c.isdigit() for c in part):
                alt_skus.add(part.lower())
    alt_skus.discard(style_code.lower())

    for alt_sku in alt_skus:
        alt_encoded = quote(alt_sku)
        if brand_search_url and brand_domain:
            alt_brand_url = (
                f"https://{brand_domain}/search?q={alt_encoded}"
                if "www." not in brand_domain
                else f"https://www.{brand_domain}/search?q={alt_encoded}"
            )
            product_page_url = _find_product_page_from_search(alt_brand_url, alt_sku.lower(), brand_domain, headers)
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, alt_sku.lower(), headers, max_images)
                if imgs:
                    result["images"] = imgs
                    result["image_source"] = f"brand_alt_sku:{brand_domain}"
                    result["product_page_url"] = product_page_url
                    result["details"] = scrape_product_details(product_page_url, headers)
                    logger.info(f"[IMG] Brand alt-SKU hit: {len(imgs)} images from {brand_domain} (alt: '{alt_sku}')")
                    return result
        for search_url_tmpl, domain in [
            (f"https://www.ssense.com/en-dk/search?q={alt_encoded}", "ssense.com"),
            (f"https://www.endclothing.com/dk/catalogsearch/result/?q={alt_encoded}", "endclothing.com"),
        ]:
            product_page_url = _find_product_page_from_search(search_url_tmpl, alt_sku.lower(), domain, headers)
            if product_page_url:
                imgs = _get_all_images_from_product_page(product_page_url, alt_sku.lower(), headers, max_images)
                if imgs:
                    result["images"] = imgs
                    result["image_source"] = f"retailer_alt_sku:{domain}"
                    result["product_page_url"] = product_page_url
                    if not result["details"]["material"]:
                        result["details"] = scrape_product_details(product_page_url, headers)
                    logger.info(f"[IMG] Retailer alt-SKU hit: {len(imgs)} from {domain} (alt: '{alt_sku}')")
                    return result

    # ── Strategy 4: Direct brand CDN patterns (safe — no search matching risk) ──
    if "american vintage" in vendor_lower:
        found = []
        for suffix in ["_1", "-1", "_front", ""]:
            test_url = f"https://www.americanvintage-store.com/media/catalog/product/{sku_lower}{suffix}.jpg"
            try:
                resp = httpx.head(test_url, headers=headers, timeout=5, follow_redirects=True)
                if resp.is_success and "image" in resp.headers.get("content-type", ""):
                    found.append(test_url)
                    if len(found) >= max_images:
                        break
            except Exception:
                continue
        if found:
            result["images"] = found
            result["image_source"] = "cdn:americanvintage"
            logger.info(f"[IMG] CDN pattern hit: {len(found)} images for SKU '{style_code}'")
            return result

    # ── Strategy 5: Generic brand site discovery ──
    if not _time_left():
        logger.info(f"[IMG] Time budget exceeded before Strategy 5, jumping to SerpAPI for '{title}'")
        fb = _serpapi_fallback(result, vendor, style_code, title, max_images)
        return fb if fb else result

    # Many fashion brands use Shopify/standard platforms with predictable URLs.
    # Try guessing the brand's website from the vendor name.
    if vendor and not brand_search_url:
        guessed_domains = _guess_brand_domains(vendor)
        for domain in guessed_domains:
            try:
                guess_url = f"https://{domain}/search?q={sku_encoded}"
                product_page_url = _find_product_page_from_search(guess_url, sku_lower, domain, headers)
                if product_page_url:
                    imgs = _get_all_images_from_product_page(product_page_url, sku_lower, headers, max_images)
                    if imgs:
                        result["images"] = imgs
                        result["image_source"] = f"guessed_brand:{domain}"
                        result["product_page_url"] = product_page_url
                        result["details"] = scrape_product_details(product_page_url, headers)
                        logger.info(f"[IMG] Guessed brand hit: {len(imgs)} images from {domain}")
                        return result
            except Exception:
                continue

    # ── Strategy 5b: Title-based fallback on brand website ──
    # When SKU-based search fails, try searching for the product TITLE on the brand's
    # website. This catches cases where the brand uses a different SKU format than
    # what's on the invoice (e.g., internal vs public SKU).
    if title and title.lower().strip() != style_code.lower().strip() and _time_left():
        title_encoded = quote(title)
        # Collect all brand domains to try
        title_search_domains: list[tuple[str, str]] = []
        if brand_domain:
            prefix = "www." if "www." not in brand_domain else ""
            title_search_domains.append((f"https://{prefix}{brand_domain}/search?q={title_encoded}", brand_domain))
        if brand_domain_for_google and brand_domain_for_google != brand_domain:
            prefix = "www." if "www." not in brand_domain_for_google else ""
            title_search_domains.append((f"https://{prefix}{brand_domain_for_google}/search?q={title_encoded}", brand_domain_for_google))

        for title_search_url, title_domain in title_search_domains:
            if not _time_left():
                break
            try:
                # Use a relaxed matching: look for any product link from search results
                from bs4 import BeautifulSoup as _BS
                resp = httpx.get(title_search_url, headers=headers, timeout=10)
                if not resp.is_success:
                    continue
                soup = _BS(resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "").lower()
                    if any(skip in href for skip in ["/category/", "/collection/", "/blog/", "/cart", "javascript:"]):
                        continue
                    full_url = urljoin(title_search_url, link.get("href", ""))
                    if title_domain not in full_url:
                        continue
                    is_product = any(seg in href for seg in ["/product/", "/products/", "/item/", "/p/", "/shop/"])
                    link_text = link.get_text(strip=True).lower()
                    # Check if the product title appears in the link text (case-insensitive)
                    title_words = [w for w in title.lower().split() if len(w) > 2]
                    if title_words and is_product:
                        match_count = sum(1 for w in title_words if w in link_text)
                        if match_count >= len(title_words) * 0.5:  # At least half the title words match
                            imgs = _get_all_images_from_product_page(full_url, sku_lower, headers, max_images)
                            if imgs:
                                result["images"] = imgs
                                result["image_source"] = f"brand_title_search:{title_domain}"
                                result["product_page_url"] = full_url
                                result["details"] = scrape_product_details(full_url, headers)
                                logger.info(f"[IMG] Title-based brand search hit: {len(imgs)} images from {title_domain} for '{title}'")
                                return result
            except Exception:
                continue

    # ── Strategy 6: Google Images via SerpAPI (ultimate fallback) ──
    # Google has indexed virtually every product image on the internet,
    # including JS-rendered sites that our scraper can't access.
    serpapi_images = _search_google_images(vendor, style_code, title, max_images)
    if serpapi_images:
        # Validate Google Images URLs — they can be flaky CDN links
        validated = _validate_image_urls(serpapi_images)
        if validated:
            result["images"] = validated
            result["image_source"] = "google_images"
            logger.info(f"[IMG] Google Images hit: {len(validated)} validated images for '{title}' (SKU: {style_code})")
            return result
        else:
            logger.warning(f"[IMG] Google Images found {len(serpapi_images)} URLs but none were accessible")

    logger.warning(f"[IMG] No images found for '{title}' (SKU: {style_code}, vendor: {vendor})")
    return result


def find_product_images(vendor: str, style_code: str, title: str, max_images: int = 5, brand_config: dict | None = None) -> list[str]:
    """Legacy wrapper — returns just image URLs."""
    result = find_product_images_and_details(vendor, style_code, title, max_images, brand_config=brand_config)
    return result.get("images", [])


# ═══════════════════════════════════════════════
# Search results page → product page URL
# ═══════════════════════════════════════════════

def _generate_sku_variants(sku_lower: str) -> list[str]:
    """
    Generate SKU search variants from a composite SKU string.

    Splits on common delimiters (-, _, .) and produces:
    - The full SKU
    - Individual parts (for COLORCODE-MODELNUMBER patterns)
    - Versions with/without leading zeros in numeric parts
    - Reversed dash-separated parts (color-first vs model-first)

    Returns a deduplicated list ordered from most specific to least.
    """
    variants: list[str] = [sku_lower]

    # Split on common delimiters
    parts = re.split(r"[-_.]", sku_lower)
    parts = [p for p in parts if p]  # remove empty strings

    # Add individual parts (for composite SKUs like AA-UBB123)
    for part in parts:
        if part not in variants:
            variants.append(part)

    # Add full SKU without delimiters
    sku_no_delim = re.sub(r"[-_.]", "", sku_lower)
    if sku_no_delim not in variants:
        variants.append(sku_no_delim)

    # Try removing leading zeros from numeric parts
    for v in list(variants):
        stripped = re.sub(r'\b0+(\d)', r'\1', v)
        if stripped != v and stripped not in variants:
            variants.append(stripped)

    # Try reversed dash-separated parts (some brands put color first, some model first)
    if "-" in sku_lower:
        dash_parts = sku_lower.split("-")
        if len(dash_parts) == 2:
            reversed_sku = f"{dash_parts[1]}-{dash_parts[0]}"
            if reversed_sku not in variants:
                variants.append(reversed_sku)

    return variants


def _find_product_page_from_search(search_url: str, sku_lower: str, domain: str, headers: dict) -> str:
    """
    From a search results page, find a link to the actual product page.

    STRICT matching: the SKU (or its most significant part) must appear in
    the product URL or in the link text. We require the main_sku to be at
    least 3 characters and contain digits — many luxury brands use short
    style codes (e.g., "B09" for a bag).

    Returns the product page URL or empty string.
    """
    from bs4 import BeautifulSoup

    try:
        response = httpx.get(search_url, headers=headers, timeout=10)
        if not response.is_success:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        # Pre-compute the SKU matching tokens ONCE (not per-link)
        sku_parts = re.split(r"[-_/\s]", sku_lower)
        # main_sku = longest part with digits AND at least 3 chars
        main_sku = sku_lower
        if sku_parts:
            candidates = [p for p in sku_parts if len(p) >= 3 and any(c.isdigit() for c in p)]
            if candidates:
                main_sku = max(candidates, key=len)
            # If no candidate has digits, require at least 5 chars to avoid false matches
            elif any(len(p) >= 5 for p in sku_parts):
                main_sku = max((p for p in sku_parts if len(p) >= 5), key=len)

        # SKU too short to match reliably — skip link scanning
        if len(main_sku) < 3:
            logger.debug(f"[IMG] SKU too short for reliable matching: '{sku_lower}' → main_sku='{main_sku}'")
            return ""

        sku_no_dash = sku_lower.replace("-", "").replace("_", "")

        # Generate SKU variants for fuzzy matching (composite SKU splitting)
        sku_variants = _generate_sku_variants(sku_lower)
        # Also generate no-hyphen versions for URL matching (brands often strip hyphens in URLs)
        sku_variants_no_hyphen = [v.replace("-", "") for v in sku_variants if "-" in v]

        best_match: str = ""
        best_score: int = 0

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            href_lower = href.lower()
            link_text = link.get_text(strip=True).lower()

            # Skip non-product links (pagination, categories, filters)
            if any(skip in href_lower for skip in [
                "/category/", "/categories/", "/collection/", "/tag/",
                "/page/", "?page=", "/filter", "/sort", "/login",
                "/cart", "/account", "/wishlist", "javascript:",
            ]):
                continue

            # Must be on the same domain
            full_url = urljoin(search_url, href)
            if domain not in full_url:
                continue

            # Check if this looks like a product page link
            is_product_link = any(seg in href_lower for seg in [
                "/product/", "/products/", "/item/", "/p/",
                "/shopping/", "/shop/",
            ])

            href_no_dash = href_lower.replace("-", "").replace("_", "")

            # Score the match quality
            score = 0

            # Full SKU in URL — strongest signal
            if sku_lower in href_lower or sku_no_dash in href_no_dash:
                score = 100
            # Main SKU part in URL
            elif main_sku in href_lower:
                score = 80
            # Full SKU in link text
            elif sku_lower in link_text:
                score = 60
            # Main SKU in link text (only if it's a product link)
            elif is_product_link and main_sku in link_text:
                score = 40

            # Fuzzy URL matching: try SKU variants and no-hyphen versions
            if score == 0:
                # Try each SKU variant (composite parts, no-leading-zeros, reversed)
                for variant in sku_variants[1:]:  # skip first — it's the full sku already tried
                    if len(variant) >= 3 and any(c.isdigit() for c in variant):
                        if variant in href_lower:
                            score = max(score, 70)
                            break
                        if variant in link_text:
                            score = max(score, 35 if is_product_link else 0)
                            break

            if score == 0:
                # Try no-hyphen variants (brands often strip hyphens in URLs)
                for variant_nh in sku_variants_no_hyphen:
                    if len(variant_nh) >= 3 and variant_nh in href_no_dash:
                        score = max(score, 65)
                        break

            # Try matching with spaces replaced by hyphens (URLs use hyphens for spaces)
            if score == 0 and " " in sku_lower:
                sku_as_hyphen = sku_lower.replace(" ", "-")
                if sku_as_hyphen in href_lower:
                    score = 90

            if score > best_score:
                best_score = score
                best_match = full_url

        if best_match:
            logger.debug(f"[IMG] Product page matched (score={best_score}): {best_match}")
            return best_match

        # Fallback: check JSON-LD structured data for product URLs
        # Check against all SKU variants for broader matching
        all_match_tokens = [sku_lower, main_sku] + [v for v in sku_variants if len(v) >= 3]
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if item.get("@type") in ("Product", "ItemPage"):
                        url = item.get("url", "")
                        if url and any(token in url.lower() for token in all_match_tokens):
                            return url
                    for el in item.get("itemListElement", []):
                        url = el.get("url", "")
                        if url and any(token in url.lower() for token in all_match_tokens):
                            return url
                        item_data = el.get("item", {})
                        if isinstance(item_data, dict):
                            url = item_data.get("url", "")
                            if url and any(token in url.lower() for token in all_match_tokens):
                                return url
            except Exception:
                continue

    except Exception:
        logger.debug("Failed to search %s for SKU %s", search_url, sku_lower)

    return ""


# ═══════════════════════════════════════════════
# Product page → image extraction
# ═══════════════════════════════════════════════

def _get_all_images_from_product_page(
    product_url: str,
    sku_lower: str,
    headers: dict,
    max_images: int = 5,
) -> list[str]:
    """
    From a specific product page, extract images for THIS specific product only.

    Strategy:
    1. JSON-LD Product.image — most reliable, curated by the site
    2. OG image — usually the main product photo
    3. img tags — ONLY if SKU is in the URL or alt text (strict matching)

    Never collect generic "product-looking" images — that pulls in related products.
    """
    from bs4 import BeautifulSoup

    # Separate buckets: trusted (JSON-LD/OG) vs SKU-matched img tags
    trusted_imgs: list[str] = []
    sku_imgs: list[str] = []
    seen_keys: set[str] = set()

    def _dedup_key(url: str) -> str:
        """Generate aggressive dedup key — strips ALL query params, size suffixes, CDN transforms."""
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
        response = httpx.get(product_url, headers=headers, timeout=10)
        if not response.is_success:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # ── 1. JSON-LD structured data — most reliable source ──
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
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
            has_sku = (
                sku_lower in src_lower
                or sku_normalized in src_normalized
                or sku_lower in alt
            )

            if has_sku:
                full_src = src
                if src.startswith("/"):
                    full_src = urljoin(product_url, src)
                _add(full_src, sku_imgs)

    except Exception:
        logger.debug("Failed to extract images from %s", product_url)

    # Merge: trusted images first, then SKU-matched img tags
    all_candidates = trusted_imgs + sku_imgs
    if not all_candidates:
        return []

    # Score images: prefer packshots, deprioritize likely model shots.
    # Hard-reject only unambiguous model/lifestyle URL patterns (delimited by / _ -).
    # Vision verification is the real quality gate — this just pre-sorts.
    import re as _re

    # These patterns use word-boundary-like delimiters to avoid false positives.
    # E.g. "lookbook" matches but "lookup" does not; "on-model" matches but "model-number" is borderline.
    _model_reject_pattern = _re.compile(
        r"[/_\-](?:lookbook|on[_\-]?model|editorial|runway|campaign|lifestyle|worn[_\-]by|wearing|worn|styling|look)[/_\-.]",
        _re.IGNORECASE,
    )

    # Hard-reject stock photo domains — never useful for product packshots
    _stock_photo_domains = [
        "shutterstock.com", "istockphoto.com", "gettyimages.com",
        "unsplash.com", "pexels.com",
    ]

    packshot_keywords = [
        "flat", "packshot", "still", "ghost", "product",
        "detail", "close", "cut-out", "cutout", "_e", "_e_",
        "pack", "lay", "front", "back",
    ]

    # Softer model keywords — not hard-rejected, but scored very low so packshots win.
    model_soft_keywords = [
        "model", "look", "worn", "outfit",
    ]

    scored = []
    for url in all_candidates:
        url_lower = url.lower()

        # Hard-reject only very obvious model/lifestyle URL patterns
        if _model_reject_pattern.search(url_lower):
            logger.debug("[IMG] Hard-rejected model/lifestyle URL: %s", url[:120])
            continue

        # Hard-reject stock photo domains
        if any(domain in url_lower for domain in _stock_photo_domains):
            logger.debug("[IMG] Hard-rejected stock photo URL: %s", url[:120])
            continue

        score = 0

        # Boost packshots
        for kw in packshot_keywords:
            if kw in url_lower:
                score += 10

        # Soft penalty for potential model shots (Vision will handle final gate)
        for kw in model_soft_keywords:
            if kw in url_lower:
                score -= 15

        if sku_lower in url_lower:
            score += 5

        scored.append((score, url))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [url for _, url in scored]

    # Content-based deduplication: download, resize to tiny thumbnail, hash
    unique_urls = []
    seen_hashes: set[str] = set()

    for url in candidates:
        if len(unique_urls) >= max_images:
            break
        try:
            resp = httpx.get(url, headers=headers, timeout=8)
            if not resp.is_success or len(resp.content) < 1000:
                continue

            try:
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                # Skip tiny images — likely thumbnails or placeholders
                if img.size[0] < 200 or img.size[1] < 200:
                    logger.debug("[IMG] Skipped tiny image (%dx%d): %s", img.size[0], img.size[1], url[:80])
                    continue
                img = img.resize((16, 16))
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

    logger.debug(f"[IMG] Extracted {len(unique_urls)} unique images from {product_url[:80]} (from {len(candidates)} candidates)")
    return unique_urls


# ═══════════════════════════════════════════════
# Image URL validation
# ═══════════════════════════════════════════════

def _validate_image_urls(urls: list[str], max_to_check: int = 5) -> list[str]:
    """
    Verify that image URLs are actually accessible (HEAD request).
    Removes dead links, 403s, and redirects to non-image content.
    Only checks the first `max_to_check` URLs for speed.
    """
    if not urls:
        return []

    valid = []
    for url in urls[:max_to_check]:
        try:
            resp = httpx.head(
                url, headers=_DEFAULT_HEADERS,
                timeout=4, follow_redirects=True,
            )
            if resp.is_success:
                content_type = resp.headers.get("content-type", "")
                # Accept image/* and also application/octet-stream (CDNs often return this)
                if "image" in content_type or "octet-stream" in content_type or not content_type:
                    valid.append(url)
                else:
                    logger.debug(f"[IMG] URL rejected (content-type: {content_type}): {url[:80]}")
            else:
                logger.debug(f"[IMG] URL rejected (HTTP {resp.status_code}): {url[:80]}")
        except Exception:
            logger.debug(f"[IMG] URL unreachable: {url[:80]}")
    return valid


def _is_valid_product_image(url: str) -> bool:
    """
    Check if URL looks like a valid product image (not a logo, icon, or placeholder).

    NOTE: Model/lifestyle shots are NOT filtered here — they are valid product images
    for e-commerce. Only genuine junk (logos, icons, UI elements) is rejected.
    The Claude Vision verification step handles quality/identity filtering.
    """
    if not url or len(url) < 15:
        return False
    url_lower = url.lower()

    # Skip non-product images (logos, icons, UI elements, tracking pixels)
    skip_patterns = [
        "logo", "icon", "favicon", "placeholder", "spacer", "pixel", "tracking",
        "badge", "banner", "sprite", "social", "payment", "flag", "arrow",
        "swatch", "color-chip", "1x1", "blank", "loading", "spinner",
    ]
    for pattern in skip_patterns:
        if pattern in url_lower:
            return False

    is_image = any(
        url_lower.endswith(ext) or f"{ext}?" in url_lower
        for ext in [".jpg", ".jpeg", ".png", ".webp", ".avif"]
    )
    is_cdn = any(
        cdn in url_lower
        for cdn in ["cdn.", "imgix", "cloudfront", "cloudinary", "shopify", "media/", "images/"]
    )
    return is_image or is_cdn


# ═══════════════════════════════════════════════
# Image cache — skip re-scraping for known products
# ═══════════════════════════════════════════════

async def get_cached_images(org_id, vendor: str, style_code: str) -> dict | None:
    """
    Check if we have cached images for this vendor+style_code.
    Returns dict like find_product_images_and_details or None if no cache.
    Only returns cache entries < 30 days old.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.core.database import async_session
    from app.models.image_cache import ImageCache

    # Normalise org_id to UUID
    if isinstance(org_id, str):
        org_id = _uuid.UUID(org_id)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    async with async_session() as db:
        stmt = select(ImageCache).where(
            ImageCache.org_id == org_id,
            ImageCache.vendor_lower == vendor.lower().strip(),
            ImageCache.style_code_lower == style_code.lower().strip(),
            ImageCache.updated_at >= cutoff,
        )
        result = await db.execute(stmt)
        cache_entry = result.scalar_one_or_none()

        if cache_entry and cache_entry.image_urls:
            # Bump hit count
            cache_entry.hit_count = (cache_entry.hit_count or 0) + 1
            await db.commit()
            return {
                "images": cache_entry.image_urls,
                "details": cache_entry.details_json or {},
                "image_source": f"cache:{cache_entry.image_source}",
                "product_page_url": cache_entry.product_page_url or "",
                "hit_count": cache_entry.hit_count,
            }
    return None


async def save_image_cache(org_id, vendor: str, style_code: str, image_result: dict) -> None:
    """
    Save verified images to cache for future imports.
    Only caches if we actually found images.
    """
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.database import async_session
    from app.models.image_cache import ImageCache

    images = image_result.get("images", [])
    if not images:
        return

    # Normalise org_id to UUID
    if isinstance(org_id, str):
        org_id = _uuid.UUID(org_id)

    vendor_lower = vendor.lower().strip()
    style_code_lower = style_code.lower().strip()

    async with async_session() as db:
        # Upsert: update if exists, create if not
        stmt = select(ImageCache).where(
            ImageCache.org_id == org_id,
            ImageCache.vendor_lower == vendor_lower,
            ImageCache.style_code_lower == style_code_lower,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.image_urls = images
            existing.image_source = image_result.get("image_source", "")
            existing.product_page_url = image_result.get("product_page_url", "")
            existing.details_json = image_result.get("details", {})
        else:
            cache_entry = ImageCache(
                org_id=org_id,
                vendor_lower=vendor_lower,
                style_code_lower=style_code_lower,
                image_urls=images,
                image_source=image_result.get("image_source", ""),
                product_page_url=image_result.get("product_page_url", ""),
                details_json=image_result.get("details", {}),
            )
            db.add(cache_entry)

        await db.commit()


# ═══════════════════════════════════════════════
# Pipeline health check — called at startup
# ═══════════════════════════════════════════════

def log_pipeline_health():
    """
    Log a summary of the image pipeline configuration at startup.
    Counts hardcoded brand configs and retailer entries.
    """
    # Count brands by inspecting _get_brand_search_config source patterns
    # Each "if ... in vendor_lower" block = one brand config (some cover sub-brands)
    brand_configs = [
        "american vintage", "comme des (wallet)", "comme des (parfum)", "comme des / cdg",
        "acne", "norse projects", "our legacy", "maison margiela / mm6",
        "a.p.c / apc", "carhartt", "modstrom", "sunflower", "salomon",
        "new balance", "birkenstock", "service works", "alohas", "marni",
        "mizuno", "timberland", "66 north", "toteme", "parel",
        "hestra", "oamc", "sophie bille brahe", "sofie ladefoged",
        "dragon diffusion", "berner kuhl", "gabi gamel", "fichi",
        "flowerism", "flatlist", "monokel",
    ]
    num_brands = len(brand_configs)

    # Count retailers from Strategy 2 list
    retailers = [
        "ssense.com", "farfetch.com", "endclothing.com", "mrporter.com",
        "mytheresa.com", "luisaviaroma.com", "24s.com", "antonioli.eu", "ln-cc.com",
    ]
    num_retailers = len(retailers)

    logger.info(
        "Image pipeline ready: %d brand configs, %d retailers",
        num_brands, num_retailers,
    )
