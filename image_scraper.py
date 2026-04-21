"""
Image scraper for fetching product images from supplier websites.
Supports American Vintage and Comme des Garçons.
Extensible for new brands.
"""

import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

# Timeout for all requests
TIMEOUT = 10

# User agent to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def scrape_images_for_product(
    vendor: str, sku: str, title: str
) -> list[str]:
    """
    Try to find product images from the vendor's website.

    Args:
        vendor: Brand name (e.g. "AMERICAN VINTAGE", "COMME DES GARCONS")
        sku: Product SKU/article code
        title: Product title

    Returns:
        List of image URLs (may be empty)
    """
    vendor_lower = vendor.lower().strip()

    try:
        if "american vintage" in vendor_lower:
            return _scrape_american_vintage(sku, title)
        elif "comme des" in vendor_lower or "cdg" in vendor_lower:
            return _scrape_cdg(sku, title)
        else:
            # Generic: try Google Images search as fallback
            return _search_generic(vendor, sku, title)
    except Exception:
        return []


def _scrape_american_vintage(sku: str, title: str) -> list[str]:
    """
    Scrape product images from American Vintage website.
    Their product pages follow patterns like:
    https://www.americanvintage-store.com/en/search?q=SKU
    """
    search_url = f"https://www.americanvintage-store.com/en/search?q={quote(sku)}"

    try:
        response = requests.get(search_url, headers=HEADERS, timeout=TIMEOUT)
        if not response.ok:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # Look for product images in search results
        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if src and any(
                keyword in src.lower()
                for keyword in ["product", "catalog", "media"]
            ):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://www.americanvintage-store.com" + src
                if src not in images:
                    images.append(src)

        # Also try to find and follow the product page link
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if sku.lower() in href.lower() or any(
                part in href.lower()
                for part in title.lower().split()[:2]
            ):
                product_url = urljoin(
                    "https://www.americanvintage-store.com", href
                )
                product_images = _get_images_from_page(product_url)
                images.extend(
                    [img for img in product_images if img not in images]
                )
                break

        return images[:5]  # Max 5 images

    except Exception:
        return []


def _scrape_cdg(sku: str, title: str) -> list[str]:
    """
    Scrape product images from Comme des Garçons / Dover Street Market.
    CDG products are often found on doverstreetmarket.com or
    comme-des-garcons.com
    """
    # Try Dover Street Market search
    search_url = f"https://shop.doverstreetmarket.com/search?q={quote(sku)}"

    try:
        response = requests.get(search_url, headers=HEADERS, timeout=TIMEOUT)
        if not response.ok:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if src and any(
                keyword in src.lower()
                for keyword in ["product", "cdn.shopify", "files"]
            ):
                if src.startswith("//"):
                    src = "https:" + src
                if src not in images:
                    images.append(src)

        return images[:5]

    except Exception:
        return []


def _get_images_from_page(url: str) -> list[str]:
    """Extract product images from a specific product page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if not response.ok:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        images = []

        # Look for Open Graph images first (usually the best quality)
        for meta in soup.find_all("meta", property="og:image"):
            content = meta.get("content", "")
            if content:
                images.append(content)

        # Then look for product images in the page
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if src and any(
                keyword in src.lower()
                for keyword in ["product", "large", "zoom", "hero"]
            ):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = urljoin(url, src)
                if src not in images:
                    images.append(src)

        return images[:5]

    except Exception:
        return []


def _search_generic(vendor: str, sku: str, title: str) -> list[str]:
    """
    Generic fallback: try to find images via the brand's website.
    This is a best-effort approach.
    """
    # Build a search-friendly brand URL
    brand_slug = vendor.lower().replace(" ", "-").replace("'", "")
    possible_domains = [
        f"https://www.{brand_slug}.com",
        f"https://{brand_slug}.com",
    ]

    for domain in possible_domains:
        try:
            search_url = f"{domain}/search?q={quote(sku)}"
            response = requests.get(
                search_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True
            )
            if response.ok:
                images = _get_images_from_page(response.url)
                if images:
                    return images
        except Exception:
            continue

    return []
