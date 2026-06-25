"""
Shopify endpoints — OAuth flow + push products.
"""

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db, async_session
from app.core.security import encrypt_token, decrypt_token
from app.models.user import User
from app.models.shopify_connection import ShopifyConnection
from app.models.import_record import Import
from app.models.import_product import ImportProduct
from app.models.product_image import ProductImage
from app.models.oauth_nonce import OAuthNonce, NONCE_TTL
from app.services.shopify_service import push_product_to_shopify

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()


def _fetch_publications_sync(shopify: "ShopifyGraphQL") -> list[dict]:
    """Fetch all publications (sales channels) from Shopify (sync)."""
    try:
        result = shopify.execute("""
            { publications(first: 20) { edges { node { id name } } } }
        """)
        return [
            {"id": e["node"]["id"], "name": e["node"]["name"]}
            for e in result.get("data", {}).get("publications", {}).get("edges", [])
        ]
    except Exception:
        return []


async def _fetch_publications(shopify: "ShopifyGraphQL") -> list[dict]:
    """Fetch all publications (sales channels) from Shopify."""
    return await asyncio.to_thread(_fetch_publications_sync, shopify)


def _fetch_collections_sync(shopify: "ShopifyGraphQL") -> list[dict]:
    """Fetch all smart collections from Shopify (sync)."""
    try:
        result = shopify.execute("""
            { collections(first: 250, query: "collection_type:smart") {
                edges { node { id title handle ruleSet { rules { column relation condition } } } }
            } }
        """)
        return [
            {
                "id": e["node"]["id"],
                "title": e["node"]["title"],
                "handle": e["node"].get("handle", ""),
                "rules": (e["node"].get("ruleSet") or {}).get("rules", []),
            }
            for e in result.get("data", {}).get("collections", {}).get("edges", [])
        ]
    except Exception:
        return []


async def _fetch_collections(shopify: "ShopifyGraphQL") -> list[dict]:
    """Fetch all smart collections from Shopify."""
    return await asyncio.to_thread(_fetch_collections_sync, shopify)


def _fetch_primary_location_sync(shopify: "ShopifyGraphQL") -> str:
    """Fetch the primary location ID from Shopify (sync)."""
    try:
        result = shopify.execute("""
            { locations(first: 1) { edges { node { id name } } } }
        """)
        edges = result.get("data", {}).get("locations", {}).get("edges", [])
        if edges:
            return edges[0]["node"]["id"]
    except Exception:
        pass
    return ""


async def _fetch_primary_location(shopify: "ShopifyGraphQL") -> str:
    """Fetch the primary location ID from Shopify."""
    return await asyncio.to_thread(_fetch_primary_location_sync, shopify)


def _search_product_by_sku_sync(shopify, sku):
    return shopify.search_product_by_sku(sku)


async def _search_product_by_sku(shopify, sku):
    return await asyncio.to_thread(_search_product_by_sku_sync, shopify, sku)


class ShopifyCircuitOpen(Exception):
    """Raised when the Shopify circuit breaker is open (too many consecutive failures)."""
    pass


class ShopifyGraphQL:
    """Lightweight Shopify GraphQL client with circuit breaker protection.

    Circuit breaker: after CIRCUIT_THRESHOLD consecutive failures, the circuit
    opens and all subsequent calls fail immediately for CIRCUIT_COOLDOWN seconds.
    This prevents hammering a down Shopify API and blocking the entire import.
    """

    CIRCUIT_THRESHOLD = 5        # consecutive failures before opening circuit
    CIRCUIT_COOLDOWN = 60        # seconds to wait before retrying after circuit opens

    def __init__(self, shop_domain: str, access_token: str):
        self.shop_domain = shop_domain
        self.access_token = access_token
        self.api_version = "2024-10"
        self.url = f"https://{shop_domain}/admin/api/{self.api_version}/graphql.json"
        self._category_cache: dict[str, str] = {}  # taxonomy fullName → GID
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        # Circuit breaker state
        self._consecutive_failures = 0
        self._circuit_opened_at: float | None = None

    def _check_circuit(self):
        """Raise if circuit is open. Auto-reset after cooldown."""
        if self._circuit_opened_at is not None:
            elapsed = time.time() - self._circuit_opened_at
            if elapsed < self.CIRCUIT_COOLDOWN:
                raise ShopifyCircuitOpen(
                    f"Shopify circuit breaker open — {int(self.CIRCUIT_COOLDOWN - elapsed)}s "
                    f"remaining. Last {self.CIRCUIT_THRESHOLD} API calls failed consecutively."
                )
            # Cooldown expired — half-open: allow one attempt
            logger.info("Shopify circuit breaker half-open — attempting recovery request")
            self._circuit_opened_at = None
            self._consecutive_failures = 0

    def _record_success(self):
        self._consecutive_failures = 0
        if self._circuit_opened_at is not None:
            logger.info("Shopify circuit breaker closed — API recovered")
            self._circuit_opened_at = None

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.CIRCUIT_THRESHOLD:
            self._circuit_opened_at = time.time()
            logger.error(
                f"Shopify circuit breaker OPEN — {self.CIRCUIT_THRESHOLD} consecutive failures. "
                f"All calls will fail fast for {self.CIRCUIT_COOLDOWN}s."
            )

    def validate_token(self) -> bool:
        """Quick validation that the access token is still valid.
        Returns True if token works, False if expired/revoked.
        """
        try:
            result = self.execute("{ shop { name } }")
            return bool(result.get("data", {}).get("shop", {}).get("name"))
        except ShopifyCircuitOpen:
            raise
        except Exception as e:
            if "401" in str(e) or "Unauthorized" in str(e) or "403" in str(e):
                return False
            # Other errors (network, etc.) — token might be fine
            logger.warning(f"Token validation inconclusive: {e}")
            return True  # Give benefit of the doubt

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query synchronously with rate limit handling and circuit breaker."""
        self._check_circuit()

        import requests
        max_retries = 5
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    self.url,
                    json={"query": query, "variables": variables or {}},
                    headers=self.headers,
                    timeout=30,
                )

                # Auth failures — token is expired/revoked
                if resp.status_code in (401, 403):
                    self._record_failure()
                    raise Exception(
                        f"Shopify authentication failed (HTTP {resp.status_code}). "
                        f"Access token may be expired or revoked."
                    )

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2))
                    logger.warning(f"Shopify rate limited, retrying in {retry_after}s")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Check for THROTTLED error in GraphQL response
                errors = data.get("errors", [])
                if errors and any("THROTTLED" in str(e) for e in errors):
                    wait = 2 ** attempt
                    logger.warning(f"Shopify throttled, retrying in {wait}s")
                    time.sleep(wait)
                    continue

                if errors:
                    # GraphQL user errors are not connection failures — don't trip breaker
                    raise Exception(f"GraphQL errors: {errors}")

                self._record_success()
                return data

            except ShopifyCircuitOpen:
                raise
            except requests.exceptions.ConnectionError as e:
                self._record_failure()
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Shopify connection error (attempt {attempt + 1}): {e}. Retrying in {wait}s")
                    time.sleep(wait)
                    continue
                raise Exception(f"Shopify API unreachable after {max_retries} attempts: {e}")
            except requests.exceptions.Timeout:
                self._record_failure()
                if attempt < max_retries - 1:
                    logger.warning(f"Shopify timeout (attempt {attempt + 1}). Retrying...")
                    time.sleep(2)
                    continue
                raise Exception(f"Shopify API timeout after {max_retries} attempts")

        raise Exception("Max retries exceeded for Shopify API")

    # ── Taxonomy category resolution ──────────────────────────────

    def resolve_category_gid(self, full_name: str) -> str:
        """Resolve a Shopify taxonomy category fullName to its GID.

        Uses Shopify's `taxonomy { categories(search:) }` query and caches
        the result so repeated lookups for the same category are free.

        Matching strategy (in priority order):
        1. Exact fullName match
        2. Case-insensitive fullName match
        3. Best partial match — the result whose fullName contains
           the most segments from our target path

        Returns the GID string (e.g. "gid://shopify/TaxonomyCategory/aa-1-2-3")
        or empty string if not found.
        """
        if full_name in self._category_cache:
            return self._category_cache[full_name]

        # Use the last segment as search term for a targeted query
        search_term = full_name.split(" > ")[-1]
        query = """
        query taxonomySearch($search: String!) {
            taxonomy {
                categories(first: 25, search: $search) {
                    edges {
                        node {
                            id
                            fullName
                        }
                    }
                }
            }
        }
        """
        try:
            result = self.execute(query, {"search": search_term})
            categories = result.get("data", {}).get("taxonomy", {}).get("categories", {}).get("edges", [])

            if not categories:
                logger.warning(f"Taxonomy search returned 0 results for: '{search_term}' (target: {full_name})")
                self._category_cache[full_name] = ""
                return ""

            # Pass 1: exact match
            for edge in categories:
                node = edge["node"]
                self._category_cache[node["fullName"]] = node["id"]
                if node["fullName"] == full_name:
                    return node["id"]

            # Pass 2: case-insensitive match
            full_name_lower = full_name.lower()
            for edge in categories:
                node = edge["node"]
                if node["fullName"].lower() == full_name_lower:
                    logger.info(f"Taxonomy case-insensitive match: '{full_name}' → '{node['fullName']}'")
                    self._category_cache[full_name] = node["id"]
                    return node["id"]

            # Pass 3: best partial match — count how many of our path segments
            # appear in the candidate's fullName (handles minor naming differences)
            our_segments = [s.strip().lower() for s in full_name.split(" > ")]
            best_match_id = ""
            best_match_name = ""
            best_score = 0

            for edge in categories:
                node = edge["node"]
                candidate_lower = node["fullName"].lower()
                score = sum(1 for seg in our_segments if seg in candidate_lower)
                if score > best_score:
                    best_score = score
                    best_match_id = node["id"]
                    best_match_name = node["fullName"]

            # Only accept if at least half the segments match (avoid false positives)
            if best_score >= len(our_segments) / 2 and best_match_id:
                logger.info(
                    f"Taxonomy partial match ({best_score}/{len(our_segments)} segments): "
                    f"'{full_name}' → '{best_match_name}' ({best_match_id})"
                )
                self._category_cache[full_name] = best_match_id
                return best_match_id

            # Log available options for debugging
            available = [edge["node"]["fullName"] for edge in categories[:5]]
            logger.warning(
                f"Taxonomy category not found for: '{full_name}'. "
                f"Search term: '{search_term}'. Available: {available}"
            )
            self._category_cache[full_name] = ""
            return ""
        except Exception as e:
            logger.warning(f"Failed to resolve taxonomy category '{full_name}': {e}")
            self._category_cache[full_name] = ""
            return ""

    def _check_user_errors(self, result: dict, mutation_name: str):
        """Raise if a mutation returned userErrors."""
        mutation_data = result.get("data", {}).get(mutation_name, {})
        user_errors = mutation_data.get("userErrors", [])
        if user_errors:
            msgs = "; ".join(e.get("message", str(e)) for e in user_errors)
            raise Exception(f"{mutation_name} userErrors: {msgs}")
        return mutation_data

    def create_product(self, product_input: dict) -> dict:
        """Create a product and return the product node with id and variants."""
        query = """
        mutation productCreate($input: ProductInput!) {
            productCreate(input: $input) {
                product {
                    id
                    handle
                    variants(first: 50) {
                        edges {
                            node {
                                id
                                title
                                selectedOptions { name value }
                                inventoryItem { id }
                            }
                        }
                    }
                }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {"input": product_input})
        data = self._check_user_errors(result, "productCreate")
        return data["product"]

    def update_product(self, product_id: str, fields: dict) -> dict:
        """Update an existing product (e.g. to set category after creation)."""
        query = """
        mutation productUpdate($input: ProductInput!) {
            productUpdate(input: $input) {
                product { id }
                userErrors { field message }
            }
        }
        """
        input_data = {"id": product_id, **fields}
        result = self.execute(query, {"input": input_data})
        data = self._check_user_errors(result, "productUpdate")
        return data["product"]

    def create_variants_bulk(self, product_id: str, variants: list[dict]) -> list[dict]:
        """Create multiple variants at once."""
        query = """
        mutation productVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkCreate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    title
                    selectedOptions { name value }
                    inventoryItem { id }
                }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {"productId": product_id, "variants": variants})
        data = self._check_user_errors(result, "productVariantsBulkCreate")
        return data.get("productVariants", [])

    def update_variants_bulk(self, product_id: str, variants: list[dict]) -> list[dict]:
        """Update multiple variants (price, etc.)."""
        query = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {"productId": product_id, "variants": variants})
        data = self._check_user_errors(result, "productVariantsBulkUpdate")
        return data.get("productVariants", [])

    def update_inventory_item(self, inventory_item_id: str, cost: float, sku: str,
                               country_code: str = "", hs_code: str = "",
                               tracked: bool = True, weight_grams: float = 0):
        """Update an inventory item with cost, SKU, tracking, etc."""
        inv_input: dict = {
            "cost": str(cost),
            "tracked": tracked,
        }
        if sku:
            inv_input["sku"] = sku
        if country_code:
            inv_input["countryCodeOfOrigin"] = country_code
        if hs_code:
            inv_input["harmonizedSystemCode"] = hs_code
        if weight_grams:
            inv_input["measurement"] = {
                "weight": {"value": weight_grams, "unit": "GRAMS"}
            }

        query = """
        mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem { id }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {"id": inventory_item_id, "input": inv_input})
        self._check_user_errors(result, "inventoryItemUpdate")

    def set_inventory_quantity(self, inventory_item_id: str, location_id: str, quantity: int):
        """Set absolute inventory quantity at a location (single item)."""
        self.set_inventory_quantities_batch(
            [(inventory_item_id, quantity)], location_id
        )

    def set_inventory_quantities_batch(
        self,
        items: list[tuple[str, int]],
        location_id: str,
    ):
        """Set absolute inventory quantities for MULTIPLE items in ONE API call.

        Args:
            items: List of (inventory_item_id, quantity) tuples.
            location_id: Shopify location GID.

        inventorySetOnHandQuantities accepts up to 100 setQuantities per call.
        """
        if not items:
            return
        query = """
        mutation inventorySetOnHandQuantities($input: InventorySetOnHandQuantitiesInput!) {
            inventorySetOnHandQuantities(input: $input) {
                inventoryAdjustmentGroup { reason }
                userErrors { field message }
            }
        }
        """
        # Chunk into batches of 100 (Shopify limit)
        BATCH_SIZE = 100
        for i in range(0, len(items), BATCH_SIZE):
            chunk = items[i:i + BATCH_SIZE]
            inv_input = {
                "reason": "correction",
                "setQuantities": [
                    {
                        "inventoryItemId": item_id,
                        "locationId": location_id,
                        "quantity": qty,
                    }
                    for item_id, qty in chunk
                ],
            }
            result = self.execute(query, {"input": inv_input})
            self._check_user_errors(result, "inventorySetOnHandQuantities")

    def get_inventory_levels(self, inventory_item_ids: list[str], location_id: str) -> dict[str, int]:
        """Query current on-hand quantities for a list of inventory items.
        Returns {inventory_item_id: quantity}.
        Uses batched node queries for efficiency (up to 50 per call).
        """
        if not inventory_item_ids:
            return {}

        levels: dict[str, int] = {}

        # Use Shopify's `nodes` query to fetch up to 50 items at once
        BATCH_SIZE = 50
        for i in range(0, len(inventory_item_ids), BATCH_SIZE):
            batch_ids = inventory_item_ids[i:i + BATCH_SIZE]
            nodes_query = """
            query getInventoryLevels($ids: [ID!]!) {
                nodes(ids: $ids) {
                    ... on InventoryItem {
                        id
                        inventoryLevels(first: 5) {
                            edges {
                                node {
                                    quantities(names: ["on_hand"]) {
                                        name
                                        quantity
                                    }
                                    location { id }
                                }
                            }
                        }
                    }
                }
            }
            """
            try:
                result = self.execute(nodes_query, {"ids": batch_ids})
                nodes = result.get("data", {}).get("nodes", [])
                for node in nodes:
                    if not node:
                        continue
                    item_id = node.get("id", "")
                    for edge in node.get("inventoryLevels", {}).get("edges", []):
                        level_node = edge["node"]
                        loc_id = level_node.get("location", {}).get("id", "")
                        if loc_id == location_id:
                            for q in level_node.get("quantities", []):
                                if q["name"] == "on_hand":
                                    levels[item_id] = q["quantity"]
            except Exception as e:
                logger.warning(f"Batch inventory level query failed: {e}")
                # Fallback: query individually
                for item_id in batch_ids:
                    try:
                        item_query = """
                        query getInventoryLevel($inventoryItemId: ID!) {
                            inventoryItem(id: $inventoryItemId) {
                                id
                                inventoryLevels(first: 5) {
                                    edges {
                                        node {
                                            quantities(names: ["on_hand"]) { name quantity }
                                            location { id }
                                        }
                                    }
                                }
                            }
                        }
                        """
                        res = self.execute(item_query, {"inventoryItemId": item_id})
                        item_data = res.get("data", {}).get("inventoryItem", {})
                        for edge in item_data.get("inventoryLevels", {}).get("edges", []):
                            ln = edge["node"]
                            if ln.get("location", {}).get("id", "") == location_id:
                                for q in ln.get("quantities", []):
                                    if q["name"] == "on_hand":
                                        levels[item_id] = q["quantity"]
                    except Exception:
                        pass
        return levels

    def set_metafields(self, owner_id: str, metafields: list[dict]):
        """Set metafields on a resource."""
        mf_input = []
        for mf in metafields:
            mf_input.append({
                "ownerId": owner_id,
                "namespace": mf["namespace"],
                "key": mf["key"],
                "value": mf["value"],
                "type": mf["type"],
            })

        query = """
        mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $metafields) {
                metafields { id }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {"metafields": mf_input})
        self._check_user_errors(result, "metafieldsSet")

    def add_image_by_url(self, product_id: str, image_url: str, alt_text: str = ""):
        """Add an image to a product by URL."""
        query = """
        mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
            productCreateMedia(productId: $productId, media: $media) {
                media { id }
                mediaUserErrors { field message }
            }
        }
        """
        media_input = [{
            "originalSource": image_url,
            "mediaContentType": "IMAGE",
            "alt": alt_text,
        }]
        result = self.execute(query, {"productId": product_id, "media": media_input})
        # Check mediaUserErrors instead of userErrors
        mutation_data = result.get("data", {}).get("productCreateMedia", {})
        errors = mutation_data.get("mediaUserErrors", [])
        if errors:
            msgs = "; ".join(e.get("message", str(e)) for e in errors)
            raise Exception(f"productCreateMedia errors: {msgs}")

    def publish_product_single(self, product_id: str, publication_id: str):
        """Publish a product to a single sales channel."""
        query = """
        mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
            publishablePublish(id: $id, input: $input) {
                publishable { publishedOnCurrentPublication }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {
            "id": product_id,
            "input": [{"publicationId": publication_id}],
        })
        self._check_user_errors(result, "publishablePublish")

    def fetch_metafield_definitions(self, owner_type: str = "PRODUCT") -> list[dict]:
        """Fetch all metafield definitions for a given owner type.

        Returns a list of dicts with keys: name, namespace, key, type, description.
        This allows the push service to match metafields to actual store definitions
        instead of guessing namespace/key combos.
        """
        query = """
        query metafieldDefinitions($ownerType: MetafieldOwnerType!) {
            metafieldDefinitions(first: 100, ownerType: $ownerType) {
                edges {
                    node {
                        name
                        namespace
                        key
                        type { name }
                        description
                    }
                }
            }
        }
        """
        try:
            result = self.execute(query, {"ownerType": owner_type})
            defs = []
            for edge in result.get("data", {}).get("metafieldDefinitions", {}).get("edges", []):
                node = edge["node"]
                defs.append({
                    "name": node.get("name", ""),
                    "namespace": node.get("namespace", ""),
                    "key": node.get("key", ""),
                    "type": node.get("type", {}).get("name", "single_line_text_field"),
                    "description": node.get("description", ""),
                })
            logger.info(f"Fetched {len(defs)} metafield definitions for {owner_type}")
            return defs
        except Exception as e:
            logger.warning(f"Failed to fetch metafield definitions: {e}")
            return []

    def fetch_recent_products(self, limit: int = 10) -> list[dict]:
        """Fetch recent active products with descriptions for AI reference.

        Returns list of dicts with: title, vendor, product_type, description_html.
        Used to feed the AI extraction prompt with real store examples.
        """
        query = """
        query recentProducts($first: Int!) {
            products(first: $first, sortKey: CREATED_AT, reverse: true, query: "status:active") {
                edges {
                    node {
                        title
                        vendor
                        productType
                        descriptionHtml
                    }
                }
            }
        }
        """
        try:
            result = self.execute(query, {"first": limit})
            products = []
            for edge in result.get("data", {}).get("products", {}).get("edges", []):
                node = edge["node"]
                desc = node.get("descriptionHtml", "")
                if desc and len(desc) > 50:  # Only include products with real descriptions
                    products.append({
                        "title": node.get("title", ""),
                        "vendor": node.get("vendor", ""),
                        "product_type": node.get("productType", ""),
                        "description_html": desc,
                    })
            logger.info(f"Fetched {len(products)} recent products with descriptions")
            return products
        except Exception as e:
            logger.warning(f"Failed to fetch recent products: {e}")
            return []

    def fetch_catalogs(self) -> list[dict]:
        """Fetch all market catalogs and their publication IDs."""
        query = """
        { catalogs(first: 50) {
            edges { node {
                id
                title
                publication { id }
            } }
        } }
        """
        try:
            result = self.execute(query)
            catalogs = []
            for edge in result.get("data", {}).get("catalogs", {}).get("edges", []):
                node = edge.get("node", {})
                pub = node.get("publication") or {}
                if pub.get("id"):
                    catalogs.append({
                        "id": node.get("id", ""),
                        "title": node.get("title", ""),
                        "publication_id": pub["id"],
                    })
            return catalogs
        except Exception as e:
            logger.warning(f"Failed to fetch catalogs: {e}")
            return []

    def add_product_to_collection(self, collection_id: str, product_id: str):
        """Add a product to a collection."""
        query = """
        mutation collectionAddProducts($id: ID!, $productIds: [ID!]!) {
            collectionAddProducts(id: $id, productIds: $productIds) {
                collection { id }
                userErrors { field message }
            }
        }
        """
        result = self.execute(query, {"id": collection_id, "productIds": [product_id]})
        self._check_user_errors(result, "collectionAddProducts")

    def get_translatable_content(self, resource_id: str) -> list[dict]:
        """Get translatable content for a resource."""
        query = """
        query translatableContent($resourceId: ID!) {
            translatableResource(resourceId: $resourceId) {
                translatableContent {
                    key
                    value
                    digest
                    locale
                }
            }
        }
        """
        result = self.execute(query, {"resourceId": resource_id})
        return (result.get("data", {})
                .get("translatableResource", {})
                .get("translatableContent", []))

    def search_product_by_sku(self, sku: str) -> dict | None:
        """Search for an existing product by SKU/style code. Returns product info or None."""
        if not sku or len(sku.strip()) < 2:
            return None
        query = '''
        query searchBySku($q: String!) {
            productVariants(first: 5, query: $q) {
                edges {
                    node {
                        sku
                        product {
                            id
                            title
                            handle
                            status
                        }
                    }
                }
            }
        }
        '''
        try:
            result = self.execute(query, {"q": f"sku:{sku.strip()}"})
            edges = result.get("data", {}).get("productVariants", {}).get("edges", [])
            for edge in edges:
                node = edge.get("node", {})
                variant_sku = node.get("sku", "")
                # Exact match (case-insensitive)
                if variant_sku.lower().strip() == sku.lower().strip():
                    product = node.get("product", {})
                    return {
                        "product_id": product.get("id", ""),
                        "title": product.get("title", ""),
                        "handle": product.get("handle", ""),
                        "status": product.get("status", ""),
                    }
            return None
        except Exception as e:
            logger.warning(f"SKU lookup failed for '{sku}': {e}")
            return None

    def search_product_by_handle(self, handle: str) -> dict | None:
        """Search for an existing product by URL handle. Returns product info or None."""
        if not handle or len(handle.strip()) < 2:
            return None
        query = '''
        query productByHandle($handle: String!) {
            productByHandle(handle: $handle) {
                id
                title
                handle
                status
            }
        }
        '''
        try:
            result = self.execute(query, {"handle": handle.strip()})
            product = result.get("data", {}).get("productByHandle")
            if product and product.get("id"):
                return {
                    "product_id": product["id"],
                    "title": product.get("title", ""),
                    "handle": product.get("handle", ""),
                    "status": product.get("status", ""),
                }
            return None
        except Exception as e:
            logger.warning(f"Handle lookup failed for '{handle}': {e}")
            return None

    def create_translation(self, resource_id: str, translations: list[dict], locale: str = "en"):
        """Create translations for a resource."""
        query = """
        mutation translationsRegister($resourceId: ID!, $translations: [TranslationInput!]!) {
            translationsRegister(resourceId: $resourceId, translations: $translations) {
                translations { key value locale }
                userErrors { field message }
            }
        }
        """
        trans_input = [{
            "key": t["key"],
            "value": t["value"],
            "locale": locale,
            "translatableContentDigest": t["digest"],
        } for t in translations]
        result = self.execute(query, {"resourceId": resource_id, "translations": trans_input})
        self._check_user_errors(result, "translationsRegister")


# ---------------------------------------------------------------------------
# OAuth Flow
# ---------------------------------------------------------------------------


@router.get("/install")
async def shopify_install(
    shop: str = Query(..., description="e.g. mystore.myshopify.com"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1: Redirect the user to Shopify's OAuth consent screen.
    Frontend calls this with ?shop=mystore.myshopify.com
    """
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise HTTPException(
            status_code=500,
            detail="Shopify app credentials not configured",
        )

    # Ensure proper domain format
    shop_domain = shop.strip().lower()
    if not shop_domain.endswith(".myshopify.com"):
        shop_domain = f"{shop_domain}.myshopify.com"

    # Generate nonce for CSRF protection — persisted in DB (survives redeployments)
    nonce = secrets.token_urlsafe(32)

    # Clean up expired nonces (older than 10 min)
    from datetime import datetime as _dt, timezone as _tz
    cutoff = _dt.now(_tz.utc) - NONCE_TTL
    await db.execute(sa_delete(OAuthNonce).where(OAuthNonce.created_at < cutoff))

    db.add(OAuthNonce(
        nonce=nonce,
        shop_domain=shop_domain,
        user_id=str(user.id),
        org_id=str(user.organisation_id),
    ))
    await db.flush()

    params = urlencode({
        "client_id": settings.shopify_api_key,
        "scope": settings.shopify_scopes,
        "redirect_uri": settings.shopify_redirect_uri,
        "state": nonce,
    })

    install_url = f"https://{shop_domain}/admin/oauth/authorize?{params}"
    return {"redirect_url": install_url}


@router.get("/callback")
async def shopify_callback(
    request: Request,
    shop: str = Query(...),
    code: str = Query(...),
    state: str = Query(...),
    hmac_param: str = Query(None, alias="hmac"),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 2: Shopify redirects here after user approves.
    Exchanges the code for a permanent access token.
    """
    # Verify nonce from database (persisted across restarts)
    nonce_result = await db.execute(
        select(OAuthNonce).where(OAuthNonce.nonce == state)
    )
    nonce_row = nonce_result.scalar_one_or_none()
    if not nonce_row:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    # Check expiry
    if nonce_row.is_expired:
        await db.delete(nonce_row)
        await db.flush()
        raise HTTPException(status_code=400, detail="OAuth state expired")

    # Extract nonce data (but don't delete yet — wait for HMAC verification)
    nonce_data = {
        "shop": nonce_row.shop_domain,
        "user_id": nonce_row.user_id,
        "org_id": nonce_row.org_id,
    }

    shop_domain = shop.strip().lower()
    if nonce_data["shop"] != shop_domain:
        raise HTTPException(status_code=400, detail="Shop mismatch")

    # Verify HMAC (mandatory)
    if not hmac_param:
        raise HTTPException(status_code=400, detail="Missing HMAC parameter")
    if not settings.shopify_api_secret:
        raise HTTPException(status_code=500, detail="Shopify API secret not configured")

    query_params = dict(request.query_params)
    received_hmac = query_params.pop("hmac", "")
    # Sort params and create message
    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(query_params.items()))
    computed_hmac = hmac.new(
        settings.shopify_api_secret.encode(),
        sorted_params.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_hmac, received_hmac):
        raise HTTPException(status_code=400, detail="HMAC verification failed")

    # HMAC verified — consume nonce (one-time use)
    await db.delete(nonce_row)
    await db.flush()

    # Exchange code for permanent access token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            json={
                "client_id": settings.shopify_api_key,
                "client_secret": settings.shopify_api_secret,
                "code": code,
            },
        )

    if resp.status_code != 200:
        logger.error("Shopify token exchange failed: status=%s, body=%s", resp.status_code, resp.text)
        raise HTTPException(
            status_code=400,
            detail="Failed to get access token from Shopify",
        )

    token_data = resp.json()
    access_token = token_data["access_token"]
    scopes = token_data.get("scope", settings.shopify_scopes)

    # Save connection
    org_id = uuid.UUID(nonce_data["org_id"])
    encrypted = encrypt_token(access_token)

    result = await db.execute(
        select(ShopifyConnection).where(
            ShopifyConnection.organisation_id == org_id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.shop_domain = shop_domain
        existing.access_token_encrypted = encrypted
        existing.scopes = scopes
        existing.is_active = True
    else:
        conn = ShopifyConnection(
            organisation_id=org_id,
            shop_domain=shop_domain,
            access_token_encrypted=encrypted,
            scopes=scopes,
        )
        db.add(conn)

    await db.flush()

    # Redirect back to frontend dashboard
    return RedirectResponse(
        url=f"{settings.shopify_app_url}/dashboard/shopify?connected=true",
        status_code=302,
    )


@router.post("/disconnect")
async def disconnect_shopify(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Shopify store from this organisation."""
    result = await db.execute(
        select(ShopifyConnection).where(
            ShopifyConnection.organisation_id == user.organisation_id
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No Shopify connection found")

    conn.is_active = False
    await db.flush()
    return {"disconnected": True}


# ---------------------------------------------------------------------------
# Connection Status
# ---------------------------------------------------------------------------


@router.get("/connection")
async def get_connection(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current org's Shopify connection status."""
    result = await db.execute(
        select(ShopifyConnection).where(
            ShopifyConnection.organisation_id == user.organisation_id
        )
    )
    conn = result.scalar_one_or_none()

    if not conn:
        return {"connected": False}

    return {
        "connected": True,
        "shop_domain": conn.shop_domain,
        "is_active": conn.is_active,
        "scopes": conn.scopes,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
    }


class ConnectRequest(BaseModel):
    shop_domain: str
    access_token: str


@router.post("/connect")
async def connect_shopify(
    req: ConnectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Connect a Shopify store to this organisation.
    Phase 3 will replace this with full OAuth flow.
    For now, accepts a custom app access token directly.
    """
    shop_domain = req.shop_domain
    access_token = req.access_token

    result = await db.execute(
        select(ShopifyConnection).where(
            ShopifyConnection.organisation_id == user.organisation_id
        )
    )
    existing = result.scalar_one_or_none()

    encrypted = encrypt_token(access_token)

    if existing:
        existing.shop_domain = shop_domain
        existing.access_token_encrypted = encrypted
        existing.is_active = True
    else:
        conn = ShopifyConnection(
            organisation_id=user.organisation_id,
            shop_domain=shop_domain,
            access_token_encrypted=encrypted,
        )
        db.add(conn)

    await db.flush()
    return {"connected": True, "shop_domain": shop_domain}


def restock_product_in_shopify(
    shopify: "ShopifyGraphQL",
    product_variants: list[dict],
    shopify_product_id: str,
    location_id: str,
) -> dict:
    """
    Restock an existing Shopify product: add inventory to existing variants,
    create new variants for missing sizes.
    """
    logger.info(f"Restocking product {shopify_product_id}")

    try:
        # 1. Fetch existing variants from Shopify
        query = '''
        query getProduct($id: ID!) {
            product(id: $id) {
                id
                title
                variants(first: 100) {
                    edges {
                        node {
                            id
                            title
                            sku
                            selectedOptions { name value }
                            inventoryItem { id }
                        }
                    }
                }
            }
        }
        '''
        result = shopify.execute(query, {"id": shopify_product_id})
        product_data = result.get("data", {}).get("product")
        if not product_data:
            return {"error": f"Shopify product {shopify_product_id} not found"}

        existing_variants = []
        for edge in product_data.get("variants", {}).get("edges", []):
            node = edge["node"]
            # Extract size from selectedOptions
            size = ""
            for opt in node.get("selectedOptions", []):
                if opt.get("name", "").lower() in ("size", "størrelse"):
                    size = opt.get("value", "")
                    break
            if not size:
                size = node.get("title", "Default Title")

            existing_variants.append({
                "variant_id": node["id"],
                "size": size,
                "sku": node.get("sku", ""),
                "inventory_item_id": node.get("inventoryItem", {}).get("id", ""),
            })

        # Build size -> variant lookup (case-insensitive)
        size_to_variant = {}
        for v in existing_variants:
            size_to_variant[v["size"].upper().strip()] = v

        restocked_count = 0
        created_count = 0
        errors = []

        for variant in product_variants:
            size = str(variant.get("size", "")).strip()
            quantity = int(variant.get("quantity", 0))
            if quantity <= 0:
                continue

            existing = size_to_variant.get(size.upper().strip())

            if existing and existing.get("inventory_item_id"):
                # 2a. Existing variant -> add inventory (additive)
                try:
                    adjust_query = '''
                    mutation inventoryAdjustQuantities($input: InventoryAdjustQuantitiesInput!) {
                        inventoryAdjustQuantities(input: $input) {
                            userErrors { field message }
                        }
                    }
                    '''
                    adjust_input = {
                        "reason": "received",
                        "name": "available",
                        "changes": [{
                            "delta": quantity,
                            "inventoryItemId": existing["inventory_item_id"],
                            "locationId": location_id,
                        }]
                    }
                    shopify.execute(adjust_query, {"input": adjust_input})
                    restocked_count += 1
                    logger.info(f"  Restocked {size}: +{quantity}")
                except Exception as e:
                    errors.append(f"Inventory adjust for {size}: {e}")
                    logger.error(f"  Failed to restock {size}: {e}")
            else:
                # 2b. New size -> create variant on existing product
                try:
                    ean = variant.get("ean", "")
                    new_variant_input = {
                        "optionValues": [{"name": size, "optionName": "Size"}],
                    }
                    if ean:
                        new_variant_input["barcode"] = ean

                    create_result = shopify.create_variants_bulk(
                        shopify_product_id, [new_variant_input]
                    )

                    # Set inventory on newly created variant
                    if create_result:
                        new_inv_item_id = create_result[0].get("inventoryItem", {}).get("id", "")
                        if new_inv_item_id and location_id:
                            try:
                                set_query = '''
                                mutation inventorySetOnHandQuantities($input: InventorySetOnHandQuantitiesInput!) {
                                    inventorySetOnHandQuantities(input: $input) {
                                        userErrors { field message }
                                    }
                                }
                                '''
                                set_input = {
                                    "reason": "received",
                                    "setQuantities": [{
                                        "inventoryItemId": new_inv_item_id,
                                        "locationId": location_id,
                                        "quantity": quantity,
                                    }]
                                }
                                shopify.execute(set_query, {"input": set_input})
                            except Exception:
                                pass  # Non-critical: variant created, inventory failed

                    created_count += 1
                    logger.info(f"  Created new variant {size}: qty {quantity}")
                except Exception as e:
                    errors.append(f"Create variant {size}: {e}")
                    logger.error(f"  Failed to create variant {size}: {e}")

        logger.info(f"Restock complete: {restocked_count} restocked, {created_count} new variants, {len(errors)} errors")

        return {
            "product_id": shopify_product_id,
            "restocked": True,
            "restocked_count": restocked_count,
            "created_count": created_count,
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"Restock failed for {shopify_product_id}: {e}")
        return {"error": str(e)}


async def _run_push(import_id: uuid.UUID, org_id: uuid.UUID):
    """Background task: push all approved products to Shopify."""
    async with async_session() as db:
        pushed = 0
        errors = 0

        try:
            # Get Shopify connection
            conn_result = await db.execute(
                select(ShopifyConnection).where(
                    ShopifyConnection.organisation_id == org_id,
                    ShopifyConnection.is_active == True,
                )
            )
            conn = conn_result.scalar_one_or_none()
            if not conn:
                raise Exception("No active Shopify connection")

            access_token = decrypt_token(conn.access_token_encrypted)
            shopify = ShopifyGraphQL(conn.shop_domain, access_token)

            # Validate token before starting expensive push operations
            token_valid = await asyncio.to_thread(shopify.validate_token)
            if not token_valid:
                raise Exception(
                    "Shopify access token er udløbet eller tilbagekaldt. "
                    "Gå til Shopify-indstillinger og forbind din butik igen."
                )

            # Get import for eur_rate
            imp_result = await db.execute(
                select(Import).where(Import.id == import_id)
            )
            imp = imp_result.scalar_one()

            # Fetch Shopify store data needed for push
            publications = await _fetch_publications(shopify)
            collections = await _fetch_collections(shopify)
            location_id = await _fetch_primary_location(shopify)
            catalogs = await asyncio.to_thread(shopify.fetch_catalogs)
            if catalogs:
                logger.info(f"Found {len(catalogs)} market catalogs: {[c['title'] for c in catalogs]}")

            # Pre-load metafield definitions once (Opt 5)
            metafield_defs = await asyncio.to_thread(
                shopify.fetch_metafield_definitions, "PRODUCT"
            )

            # Get approved products
            products_result = await db.execute(
                select(ImportProduct).where(
                    ImportProduct.import_id == import_id,
                    ImportProduct.status == "approved",
                )
            )
            products = products_result.scalars().all()
            logger.info(
                f"[PUSH] Found {len(products)} approved products for import {import_id}"
            )

            # Debug: log all product statuses for this import
            all_prods_result = await db.execute(
                select(ImportProduct.id, ImportProduct.status, ImportProduct.title).where(
                    ImportProduct.import_id == import_id,
                )
            )
            for pid, pstatus, ptitle in all_prods_result.all():
                logger.info(f"[PUSH DEBUG] Product '{ptitle}' ({pid}): status={pstatus}")

            # Get existing tags for dedup
            existing_tags: list[str] = []

            for product in products:
                try:
                    # Skip already-pushed products
                    if product.shopify_product_id and product.status == "pushed":
                        logger.info(f"Skipping already-pushed product: {product.title}")
                        continue

                    # --- Pre-push safety gate ---
                    # Two-layer duplicate check before creating a new product:
                    #   1. SKU-based: catches same style_code already in Shopify
                    #   2. Handle-based: catches same product+color combo (URL slug)
                    # If either matches, auto-convert to restock to prevent duplicates.
                    if not product.is_restock and product.style_code:
                        try:
                            safety_match = await _search_product_by_sku(
                                shopify, product.style_code.strip()
                            )
                            if safety_match and safety_match.get("product_id"):
                                # Verify color match: same SKU in a different color is NOT a restock
                                p_color = (product.color or "").lower().strip()
                                match_handle = (safety_match.get("handle") or "").lower()
                                color_mismatch = False
                                if p_color and match_handle:
                                    import re as _re
                                    color_slug = _re.sub(r"[^a-z0-9]+", "-", p_color).strip("-")
                                    if color_slug and color_slug not in match_handle:
                                        color_mismatch = True
                                        logger.info(
                                            f"[SAFETY] SKU match but color mismatch: '{product.title}' ({p_color}) "
                                            f"≠ '{safety_match['title']}' (handle: {match_handle}) — skipping restock"
                                        )
                                if not color_mismatch:
                                    product.is_restock = True
                                    product.shopify_match_id = safety_match["product_id"]
                                    product.shopify_match_title = safety_match["title"]
                                    logger.info(
                                        f"[SAFETY] Auto-converted '{product.title}' to restock "
                                        f"— SKU '{product.style_code}' found in Shopify as "
                                        f"'{safety_match['title']}' ({safety_match['product_id']})"
                                    )
                        except Exception as safety_err:
                            # Non-critical: if lookup fails, proceed with new product
                            logger.warning(
                                f"[SAFETY] Pre-push SKU check failed for '{product.title}': {safety_err}"
                            )

                    # Layer 2: Handle-based duplicate check
                    if not product.is_restock:
                        try:
                            # Build the same handle that shopify_service will use
                            from app.services.product_enrichment import make_handle
                            _handle = getattr(product, 'handle', '') or ''
                            if not _handle:
                                _handle = make_handle(product.vendor or '', product.title or '')
                            _color = (product.color or '').strip()
                            if _color:
                                import re as _re
                                _color_slug = _re.sub(r"[^a-z0-9]+", "-", _color.lower()).strip("-")
                                if _color_slug and _color_slug not in _handle:
                                    _handle = f"{_handle}-{_color_slug}"

                            if _handle:
                                handle_match = await asyncio.to_thread(
                                    shopify.search_product_by_handle, _handle
                                )
                                if handle_match and handle_match.get("product_id"):
                                    product.is_restock = True
                                    product.shopify_match_id = handle_match["product_id"]
                                    product.shopify_match_title = handle_match["title"]
                                    logger.info(
                                        f"[SAFETY] Auto-converted '{product.title}' to restock "
                                        f"— handle '{_handle}' already exists in Shopify as "
                                        f"'{handle_match['title']}' ({handle_match['product_id']})"
                                    )
                        except Exception as handle_err:
                            logger.warning(
                                f"[SAFETY] Handle check failed for '{product.title}': {handle_err}"
                            )

                    if product.is_restock and product.shopify_match_id:
                        # === RESTOCK: Update inventory on existing product ===
                        result = await asyncio.to_thread(
                            restock_product_in_shopify,
                            shopify=shopify,
                            product_variants=product.variants or [],
                            shopify_product_id=product.shopify_match_id,
                            location_id=location_id,
                        )

                        if result.get("product_id") or result.get("restocked"):
                            product.shopify_product_id = result.get("product_id") or product.shopify_match_id
                            product.status = "pushed"
                            pushed += 1
                        else:
                            product.status = "error"
                            product.error_message = result.get("error", "Unknown error")
                            errors += 1
                    else:
                        # === NEW PRODUCT: Create from scratch ===
                        # Idempotency guard: if product has a shopify_product_id from a
                        # previous attempt that didn't commit, verify it exists in Shopify
                        # before creating a duplicate.
                        if product.shopify_product_id:
                            try:
                                existing_check = await asyncio.to_thread(
                                    shopify.search_product_by_handle,
                                    product.shopify_product_id.split("/")[-1] if "/" in product.shopify_product_id else ""
                                )
                                if existing_check:
                                    logger.info(
                                        f"[IDEMPOTENCY] Product '{product.title}' already exists "
                                        f"in Shopify ({product.shopify_product_id}) — skipping creation"
                                    )
                                    product.status = "pushed"
                                    pushed += 1
                                    await db.commit()
                                    continue
                            except Exception:
                                pass  # If check fails, proceed with creation

                        # Build image list: uploaded images FIRST, then scraped
                        uploaded_result = await db.execute(
                            select(ProductImage)
                            .where(ProductImage.product_id == product.id)
                            .order_by(ProductImage.sort_order)
                        )
                        uploaded_images = uploaded_result.scalars().all()

                        base_url = settings.public_base_url.rstrip("/")
                        uploaded_urls = [
                            f"{base_url}/api/v1/images/{img.file_path}"
                            for img in uploaded_images
                        ]
                        scraped_urls = product.images or []

                        # If user uploaded images, those take full priority
                        # Otherwise fall back to scraped
                        if uploaded_urls:
                            final_image_urls = uploaded_urls[:5]
                            logger.info(
                                f"Using {len(final_image_urls)} uploaded images for '{product.title}'"
                            )
                        else:
                            final_image_urls = scraped_urls[:5]

                        product_data = {
                            "title": product.title,
                            "vendor": product.vendor,
                            "type": product.product_type,
                            "product_type": product.product_type,
                            "product_type_da": product.product_type,
                            "description_da": product.description_da,
                            "details": product.description_da,  # Used by build_description_da
                            "details_en": getattr(product, 'description_en', ''),  # English description for translation
                            "style_code": product.style_code,
                            "color": product.color,
                            "color_code": product.color_code,
                            "color_original": getattr(product, 'color_original', ''),
                            "material": getattr(product, 'material', ''),
                            "gender": getattr(product, 'gender', ''),
                            "season": getattr(product, 'season', ''),
                            "country_of_origin": getattr(product, 'country_of_origin', ''),
                            "hs_code": getattr(product, 'hs_code', ''),
                            "ai_tags": getattr(product, 'ai_tags', []) or [],
                            "seo_keywords": getattr(product, 'seo_keywords', []) or [],
                            "is_test": imp.is_test,
                            "handle": getattr(product, 'handle', ''),
                            "cost_price_eur": product.cost_price_eur,
                            "cost_price_dkk": product.cost_price_dkk,
                            "retail_price_dkk": product.retail_price_dkk,
                            "variants": product.variants or [],
                            "images": final_image_urls,
                            "image_urls": final_image_urls,
                        }

                        result = await push_product_to_shopify(
                            shopify=shopify,
                            product=product_data,
                            eur_rate=imp.eur_rate,
                            publications=publications,
                            collections=collections,
                            location_id=location_id,
                            metafield_defs=metafield_defs,
                            existing_tags=existing_tags,
                            catalogs=catalogs,
                        )

                        if result.get("product_id"):
                            product.shopify_product_id = result["product_id"]
                            product.status = "pushed"
                            pushed += 1
                        else:
                            product.status = "error"
                            product.error_message = "; ".join(result.get("errors", []))
                            errors += 1

                except ShopifyCircuitOpen as circuit_err:
                    # Circuit breaker tripped — abort remaining products immediately
                    product.status = "error"
                    product.error_message = str(circuit_err)[:500]
                    errors += 1
                    await db.commit()
                    logger.error(
                        f"[PUSH] Circuit breaker open — aborting remaining products. "
                        f"Pushed {pushed}, errors {errors}."
                    )
                    break  # Exit the product loop — don't hammer a dead API

                except Exception as e:
                    product.status = "error"
                    product.error_message = str(e)[:500]
                    errors += 1

                # Commit after each product so progress is saved
                await db.commit()

            # Update import record
            imp_result = await db.execute(
                select(Import).where(Import.id == import_id)
            )
            imp = imp_result.scalar_one()
            imp.products_pushed = pushed
            from datetime import datetime, timezone
            if pushed > 0:
                imp.status = "completed"
                imp.completed_at = datetime.now(timezone.utc)
            elif errors > 0:
                imp.status = "failed"
                imp.error_message = f"{errors} produkt(er) fejlede under push til Shopify"
            else:
                imp.status = "failed"
                imp.error_message = "Ingen produkter fundet til push (mulig synkroniseringsfejl)"
            await db.commit()

        except Exception as e:
            await db.rollback()
            async with async_session() as error_db:
                imp_result = await error_db.execute(
                    select(Import).where(Import.id == import_id)
                )
                imp = imp_result.scalar_one()
                imp.status = "failed"
                imp.error_message = str(e)[:1000]
                await error_db.commit()


class PushRequest(BaseModel):
    product_ids: list[str] = []


@router.post("/push/{import_id}")
async def push_to_shopify(
    import_id: str,
    background_tasks: BackgroundTasks,
    body: PushRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Push selected products from an import to Shopify."""
    # Verify Shopify connection
    conn_result = await db.execute(
        select(ShopifyConnection).where(
            ShopifyConnection.organisation_id == user.organisation_id,
            ShopifyConnection.is_active == True,
        )
    )
    conn = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status_code=400,
            detail="No active Shopify connection. Connect your store first.",
        )

    # Validate UUID
    try:
        import_uuid = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")

    # Get import
    imp_result = await db.execute(
        select(Import).where(
            Import.id == import_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    imp = imp_result.scalar_one_or_none()
    if not imp:
        raise HTTPException(status_code=404, detail="Import not found")

    if imp.status not in ("review", "completed", "failed", "pushing"):
        raise HTTPException(
            status_code=400,
            detail=f"Import must be in 'review', 'completed', 'failed', or 'pushing' status to push (current: '{imp.status}')",
        )

    # Mark selected products as approved (frontend sends product_ids)
    selected_ids = body.product_ids if body and body.product_ids else []
    if selected_ids:
        selected_uuids = [uuid.UUID(pid) for pid in selected_ids]
        selected_result = await db.execute(
            select(ImportProduct).where(
                ImportProduct.import_id == imp.id,
                ImportProduct.id.in_(selected_uuids),
                ImportProduct.status.in_(("pending", "review")),
            )
        )
        for p in selected_result.scalars().all():
            p.status = "approved"
        await db.flush()
    else:
        # No specific IDs: approve all pending products
        pending_result = await db.execute(
            select(ImportProduct).where(
                ImportProduct.import_id == imp.id,
                ImportProduct.status.in_(("pending", "review")),
            )
        )
        for p in pending_result.scalars().all():
            p.status = "approved"
        await db.flush()

    # Reset errored products back to approved for re-push
    error_products_result = await db.execute(
        select(ImportProduct).where(
            ImportProduct.import_id == imp.id,
            ImportProduct.status == "error",
        )
    )
    for p in error_products_result.scalars().all():
        p.status = "approved"
        p.error_message = None
    await db.flush()

    # Get approved products count (includes just-reset ones)
    products_result = await db.execute(
        select(ImportProduct).where(
            ImportProduct.import_id == imp.id,
            ImportProduct.status == "approved",
        )
    )
    products = products_result.scalars().all()

    if not products:
        raise HTTPException(status_code=400, detail="No approved products to push")

    imp.status = "pushing"

    # CRITICAL: Commit BEFORE starting background task.
    # BackgroundTasks run before dependency cleanup (get_db commit),
    # so _run_push's new session would see stale data if we only flush.
    await db.commit()

    # Run push in background
    background_tasks.add_task(_run_push, imp.id, user.organisation_id)

    return {
        "import_id": str(imp.id),
        "status": "pushing",
        "products_to_push": len(products),
    }
