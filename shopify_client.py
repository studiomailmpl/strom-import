"""
Shopify Admin API client for creating products with variants, images, tags, and SEO.
Uses the REST Admin API (2024-01).
"""

import requests
import time
import base64
from urllib.parse import urljoin


class ShopifyClient:
    def __init__(self, store_name: str, access_token: str):
        self.store_name = store_name
        self.access_token = access_token
        self.base_url = f"https://{store_name}.myshopify.com/admin/api/2024-01"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make an API request with rate limiting."""
        url = f"{self.base_url}/{endpoint}.json"
        response = requests.request(
            method, url, headers=self.headers, json=data, timeout=30
        )

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 2))
            time.sleep(retry_after)
            return self._request(method, endpoint, data)

        if not response.ok:
            error_body = response.json() if response.content else {}
            error_msg = error_body.get("errors", response.text)
            raise Exception(
                f"Shopify API fejl ({response.status_code}): {error_msg}"
            )

        return response.json()

    def create_product(self, product_data: dict) -> int:
        """
        Create a product in Shopify with variants, images, tags, and SEO.

        Args:
            product_data: Dict with keys matching our extraction format

        Returns:
            Shopify product ID
        """
        # Build variants
        variants = []
        for variant in product_data.get("variants", []):
            v = {
                "option1": variant.get("size", "One Size"),
                "price": str(product_data.get("retail_price", 0)),
                "sku": f"{product_data.get('sku', '')}-{variant.get('size', '')}",
                "inventory_management": "shopify",
                "inventory_quantity": variant.get("quantity", 0),
                "requires_shipping": True,
                "taxable": True,
            }

            # Add compare_at_price if set
            if product_data.get("compare_at_price"):
                v["compare_at_price"] = str(product_data["compare_at_price"])

            # Add cost price
            if product_data.get("cost_price"):
                v["cost"] = str(product_data["cost_price"])

            variants.append(v)

        # If no variants extracted, create a default one
        if not variants:
            variants = [
                {
                    "price": str(product_data.get("retail_price", 0)),
                    "sku": product_data.get("sku", ""),
                    "inventory_management": "shopify",
                    "requires_shipping": True,
                    "taxable": True,
                }
            ]

        # Build tags
        tags = product_data.get("tags", [])
        if isinstance(tags, list):
            tags_str = ", ".join(tags)
        else:
            tags_str = tags

        # Build product body
        body = {
            "product": {
                "title": product_data.get("title", "Untitled"),
                "body_html": product_data.get("description", ""),
                "vendor": product_data.get("vendor", ""),
                "product_type": product_data.get("product_type", ""),
                "tags": tags_str,
                "status": "draft",  # Always create as draft for safety
                "options": [{"name": "Size"}],
                "variants": variants,
                # SEO / metafields
                "metafields_global_title_tag": product_data.get("seo_title", ""),
                "metafields_global_description_tag": product_data.get(
                    "seo_description", ""
                ),
            }
        }

        # Create the product
        result = self._request("POST", "products", body)
        product_id = result["product"]["id"]

        # Upload images if we have URLs
        image_urls = product_data.get("image_urls", [])
        for img_url in image_urls:
            try:
                self._add_image_from_url(product_id, img_url)
            except Exception:
                pass  # Don't fail the whole product if an image fails

        return product_id

    def _add_image_from_url(self, product_id: int, image_url: str):
        """Add an image to a product from a URL."""
        body = {"image": {"src": image_url}}
        self._request("POST", f"products/{product_id}/images", body)

    def _add_image_from_base64(
        self, product_id: int, image_data: str, filename: str = "product.jpg"
    ):
        """Add an image to a product from base64 data."""
        body = {"image": {"attachment": image_data, "filename": filename}}
        self._request("POST", f"products/{product_id}/images", body)

    def get_collections(self) -> list[dict]:
        """Get all custom collections."""
        result = self._request("GET", "custom_collections")
        return result.get("custom_collections", [])

    def add_product_to_collection(self, product_id: int, collection_id: int):
        """Add a product to a collection."""
        body = {
            "collect": {"product_id": product_id, "collection_id": collection_id}
        }
        self._request("POST", "collects", body)

    def test_connection(self) -> bool:
        """Test the API connection."""
        try:
            self._request("GET", "shop")
            return True
        except Exception:
            return False
