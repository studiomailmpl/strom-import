"""
Shopify GraphQL Admin API client for STRØM Store.
Handles: product creation, variants, metafields, images, publishing, translations.
"""

import requests
import time
import json
import re


class ShopifyGraphQL:
    def __init__(self, store: str, access_token: str):
        self.store = store
        self.url = f"https://{store}.myshopify.com/admin/api/2024-10/graphql.json"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

    def _execute(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query with rate-limit handling."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(3):
            response = requests.post(
                self.url, headers=self.headers, json=payload, timeout=30
            )

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 2))
                time.sleep(retry_after)
                continue

            if not response.ok:
                raise Exception(f"Shopify API HTTP {response.status_code}: {response.text}")

            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL errors: {json.dumps(data['errors'])}")

            return data.get("data", {})

        raise Exception("Shopify API: max retries exceeded")

    # ─────────────────────────────────────────
    # Fetch existing data
    # ─────────────────────────────────────────

    def fetch_all_tags(self) -> list[str]:
        """Fetch all unique product tags from the store."""
        tags = set()
        cursor = None
        has_next = True

        while has_next:
            after = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
                products(first: 250{after}) {{
                    edges {{
                        node {{ tags }}
                        cursor
                    }}
                    pageInfo {{ hasNextPage }}
                }}
            }}
            """
            data = self._execute(query)
            products = data.get("products", {})

            for edge in products.get("edges", []):
                for tag in edge["node"].get("tags", []):
                    tags.add(tag)
                cursor = edge["cursor"]

            has_next = products.get("pageInfo", {}).get("hasNextPage", False)

        return sorted(tags)

    def fetch_all_vendors(self) -> list[str]:
        """Fetch all unique vendors."""
        vendors = set()
        cursor = None
        has_next = True

        while has_next:
            after = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
                products(first: 250{after}) {{
                    edges {{
                        node {{ vendor }}
                        cursor
                    }}
                    pageInfo {{ hasNextPage }}
                }}
            }}
            """
            data = self._execute(query)
            products = data.get("products", {})

            for edge in products.get("edges", []):
                vendor = edge["node"].get("vendor", "")
                if vendor:
                    vendors.add(vendor)
                cursor = edge["cursor"]

            has_next = products.get("pageInfo", {}).get("hasNextPage", False)

        return sorted(vendors)

    def fetch_publications(self) -> list[dict]:
        """Fetch all available publication channels AND catalog publications."""
        # First get regular publications (sales channels)
        query = """
        {
            publications(first: 50) {
                edges {
                    node {
                        id
                        name
                    }
                }
            }
        }
        """
        data = self._execute(query)
        pubs = [
            {"id": edge["node"]["id"], "name": edge["node"]["name"]}
            for edge in data.get("publications", {}).get("edges", [])
        ]

        # Also fetch catalog publications (region/market catalogs like Danmark, stromstore.com, etc.)
        catalog_query = """
        {
            catalogs(first: 50) {
                edges {
                    node {
                        id
                        title
                        publication {
                            id
                        }
                    }
                }
            }
        }
        """
        try:
            catalog_data = self._execute(catalog_query)
            existing_pub_ids = {p["id"] for p in pubs}
            for edge in catalog_data.get("catalogs", {}).get("edges", []):
                node = edge["node"]
                pub = node.get("publication")
                if pub and pub.get("id") and pub["id"] not in existing_pub_ids:
                    pubs.append({
                        "id": pub["id"],
                        "name": f"Catalog: {node.get('title', 'Unknown')}",
                    })
                    existing_pub_ids.add(pub["id"])
        except Exception:
            pass  # Catalogs API might not be available on all plans

        return pubs

    def fetch_collections(self) -> list[dict]:
        """Fetch all custom collections (for brand matching)."""
        collections = []
        cursor = None
        has_next = True

        while has_next:
            after = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
                collections(first: 250{after}) {{
                    edges {{
                        node {{
                            id
                            title
                            handle
                        }}
                        cursor
                    }}
                    pageInfo {{ hasNextPage }}
                }}
            }}
            """
            data = self._execute(query)
            cols = data.get("collections", {})

            for edge in cols.get("edges", []):
                collections.append(edge["node"])
                cursor = edge["cursor"]

            has_next = cols.get("pageInfo", {}).get("hasNextPage", False)

        return collections

    # ─────────────────────────────────────────
    # Product Creation
    # ─────────────────────────────────────────

    def create_product(self, product_input: dict) -> dict:
        """
        Create a product using the new ProductCreateInput format.
        Returns the product dict with id, variants, etc.
        """
        query = """
        mutation productCreate($product: ProductCreateInput!) {
            productCreate(product: $product) {
                product {
                    id
                    title
                    handle
                    variants(first: 50) {
                        edges {
                            node {
                                id
                                title
                                selectedOptions {
                                    name
                                    value
                                }
                                inventoryItem {
                                    id
                                }
                            }
                        }
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        data = self._execute(query, {"product": product_input})
        result = data.get("productCreate", {})

        errors = result.get("userErrors", [])
        if errors:
            error_msgs = "; ".join([f"{e['field']}: {e['message']}" for e in errors])
            raise Exception(f"Product creation failed: {error_msgs}")

        product = result.get("product", {})
        if not product:
            raise Exception("Product creation returned no product")

        return product

    def create_variants_bulk(self, product_id: str, variants: list[dict]) -> list[dict]:
        """
        Create new variants in bulk using productVariantsBulkCreate.
        Each variant needs: optionValues (list of {optionName, name}), price, etc.
        Returns list of created variant nodes.
        """
        query = """
        mutation productVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkCreate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    title
                    selectedOptions {
                        name
                        value
                    }
                    inventoryItem {
                        id
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        data = self._execute(query, {"productId": product_id, "variants": variants})
        result = data.get("productVariantsBulkCreate", {})

        errors = result.get("userErrors", [])
        if errors:
            error_msgs = "; ".join([f"{e['field']}: {e['message']}" for e in errors])
            raise Exception(f"Variant creation failed: {error_msgs}")

        return result.get("productVariants", [])

    def update_variants_bulk(self, product_id: str, variants: list[dict]) -> list[dict]:
        """
        Update variants in bulk (price, SKU, weight, etc).
        """
        query = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    title
                    inventoryItem {
                        id
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        data = self._execute(query, {"productId": product_id, "variants": variants})
        result = data.get("productVariantsBulkUpdate", {})

        errors = result.get("userErrors", [])
        if errors:
            error_msgs = "; ".join([f"{e['field']}: {e['message']}" for e in errors])
            raise Exception(f"Variant update failed: {error_msgs}")

        return result.get("productVariants", [])

    # ─────────────────────────────────────────
    # Inventory
    # ─────────────────────────────────────────

    def set_inventory_quantity(self, inventory_item_id: str, location_id: str, quantity: int):
        """Set inventory quantity for a variant at a location."""
        query = """
        mutation inventorySetOnHandQuantities($input: InventorySetOnHandQuantitiesInput!) {
            inventorySetOnHandQuantities(input: $input) {
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "input": {
                "reason": "correction",
                "setQuantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": location_id,
                        "quantity": quantity,
                    }
                ],
            }
        }
        data = self._execute(query, variables)
        errors = data.get("inventorySetOnHandQuantities", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Inventory error: {errors}")

    def get_primary_location_id(self) -> str:
        """Get the primary location ID."""
        query = """
        {
            locations(first: 1) {
                edges {
                    node { id }
                }
            }
        }
        """
        data = self._execute(query)
        edges = data.get("locations", {}).get("edges", [])
        if not edges:
            raise Exception("No locations found")
        return edges[0]["node"]["id"]

    # ─────────────────────────────────────────
    # Images
    # ─────────────────────────────────────────

    def add_image_by_url(self, product_id: str, image_url: str, alt_text: str = ""):
        """Add an image to a product from a URL."""
        query = """
        mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
            productCreateMedia(productId: $productId, media: $media) {
                media {
                    ... on MediaImage {
                        id
                        status
                    }
                }
                mediaUserErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "productId": product_id,
            "media": [
                {
                    "originalSource": image_url,
                    "alt": alt_text,
                    "mediaContentType": "IMAGE",
                }
            ],
        }
        data = self._execute(query, variables)
        errors = data.get("productCreateMedia", {}).get("mediaUserErrors", [])
        if errors:
            raise Exception(f"Image upload error: {errors}")

    # ─────────────────────────────────────────
    # Publishing
    # ─────────────────────────────────────────

    def publish_product(self, product_id: str, publication_ids: list[str]):
        """Publish a product to specified channels."""
        for pub_id in publication_ids:
            self.publish_product_single(product_id, pub_id)

    def publish_product_single(self, product_id: str, publication_id: str):
        """Publish a product to a single channel. Raises on error for proper logging."""
        query = """
        mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
            publishablePublish(id: $id, input: $input) {
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "id": product_id,
            "input": [{"publicationId": publication_id}],
        }
        data = self._execute(query, variables)
        errors = data.get("publishablePublish", {}).get("userErrors", [])
        if errors:
            error_msgs = "; ".join([f"{e['field']}: {e['message']}" for e in errors])
            raise Exception(f"Publish error: {error_msgs}")

    # ─────────────────────────────────────────
    # Metafields
    # ─────────────────────────────────────────

    def fetch_metafield_definitions(self, owner_type: str = "PRODUCT") -> list[dict]:
        """Fetch all metafield definitions for a given owner type."""
        definitions = []
        cursor = None
        has_next = True

        while has_next:
            after = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
                metafieldDefinitions(first: 50, ownerType: {owner_type}{after}) {{
                    edges {{
                        node {{
                            id
                            name
                            namespace
                            key
                            type {{
                                name
                            }}
                        }}
                        cursor
                    }}
                    pageInfo {{ hasNextPage }}
                }}
            }}
            """
            data = self._execute(query)
            defs = data.get("metafieldDefinitions", {})

            for edge in defs.get("edges", []):
                node = edge["node"]
                definitions.append({
                    "id": node["id"],
                    "name": node["name"],
                    "namespace": node["namespace"],
                    "key": node["key"],
                    "type": node["type"]["name"],
                })
                cursor = edge["cursor"]

            has_next = defs.get("pageInfo", {}).get("hasNextPage", False)

        return definitions

    def set_metafields(self, product_id: str, metafields: list[dict]):
        """Set metafields on a product."""
        query = """
        mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $metafields) {
                metafields {
                    id
                    key
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        mf_input = []
        for mf in metafields:
            mf_input.append({
                "ownerId": product_id,
                "namespace": mf["namespace"],
                "key": mf["key"],
                "value": mf["value"],
                "type": mf["type"],
            })

        data = self._execute(query, {"metafields": mf_input})
        errors = data.get("metafieldsSet", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Metafield error: {errors}")

    # ─────────────────────────────────────────
    # Translations
    # ─────────────────────────────────────────

    def create_translation(self, resource_id: str, translations: list[dict], locale: str = "en"):
        """Create translations for a resource."""
        query = """
        mutation translationsRegister($resourceId: ID!, $translations: [TranslationInput!]!) {
            translationsRegister(resourceId: $resourceId, translations: $translations) {
                userErrors {
                    field
                    message
                }
                translations {
                    key
                    value
                    locale
                }
            }
        }
        """
        trans_input = []
        for t in translations:
            trans_input.append({
                "key": t["key"],
                "value": t["value"],
                "locale": locale,
                "translatableContentDigest": t["digest"],
            })

        data = self._execute(query, {
            "resourceId": resource_id,
            "translations": trans_input,
        })
        errors = data.get("translationsRegister", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Translation error: {errors}")

    def get_translatable_content(self, resource_id: str) -> list[dict]:
        """Get translatable content and digests for a resource."""
        query = """
        query translatableResource($resourceId: ID!) {
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
        data = self._execute(query, {"resourceId": resource_id})
        return data.get("translatableResource", {}).get("translatableContent", [])

    # ─────────────────────────────────────────
    # Collections
    # ─────────────────────────────────────────

    def add_product_to_collection(self, collection_id: str, product_id: str):
        """Add a product to a collection."""
        query = """
        mutation collectionAddProducts($id: ID!, $productIds: [ID!]!) {
            collectionAddProducts(id: $id, productIds: $productIds) {
                userErrors {
                    field
                    message
                }
            }
        }
        """
        data = self._execute(query, {
            "id": collection_id,
            "productIds": [product_id],
        })
        errors = data.get("collectionAddProducts", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Collection error: {errors}")

    # ─────────────────────────────────────────
    # Inventory item update (SKU, cost, weight, origin)
    # ─────────────────────────────────────────

    def update_inventory_item(
        self,
        inventory_item_id: str,
        cost: float = None,
        sku: str = "",
        country_code: str = "",
        hs_code: str = "",
        weight_grams: float = None,
        tracked: bool = True,
    ):
        """Update SKU, cost, weight, country/HS code on an inventory item."""
        query = """
        mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem { id sku }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        item_input = {"tracked": tracked}
        if cost is not None:
            item_input["cost"] = str(cost)
        if sku:
            item_input["sku"] = sku
        if country_code:
            item_input["countryCodeOfOrigin"] = country_code
        if hs_code:
            item_input["harmonizedSystemCode"] = hs_code
        if weight_grams is not None:
            item_input["measurement"] = {
                "weight": {
                    "value": weight_grams,
                    "unit": "GRAMS",
                }
            }

        data = self._execute(query, {"id": inventory_item_id, "input": item_input})
        errors = data.get("inventoryItemUpdate", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Inventory item update error: {errors}")
