"""
Google Search Console Service — Layer 3 of the SEO keyword intelligence.

Fetches search analytics data from Google Search Console, aggregates it
per product type, and stores top-performing keywords in the database.
These keywords are then fed back into AI extraction prompts over time,
making the system smarter with each import.

Flow:
1. Tenant configures Search Console access (OAuth + property URL)
2. Periodic sync job fetches search analytics (last 28 days)
3. Landing page URLs are mapped to product types via Shopify URL patterns
4. Top keywords per product type are stored in KeywordPerformance
5. During import, get_historical_keywords() provides context to AI prompts

OAuth tokens must be managed externally (Google OAuth2 flow) and stored
encrypted in SearchConsoleConfig.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keyword_performance import KeywordPerformance, SearchConsoleConfig

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Product type mapping from Shopify URLs
# ═══════════════════════════════════════════════

# Maps URL patterns to product type (Danish, matching our TYPE_MAP_DA)
_URL_TO_PRODUCT_TYPE: dict[str, str] = {
    "/collections/bukser": "Bukser",
    "/collections/shorts": "Shorts",
    "/collections/skjorter": "Skjorter",
    "/collections/t-shirts": "T-Shirts",
    "/collections/strik": "Strik",
    "/collections/jakker": "Jakker",
    "/collections/blazere": "Blazere",
    "/collections/kjoler": "Kjoler",
    "/collections/nederdele": "Nederdele",
    "/collections/toppe": "Toppe",
    "/collections/bluser": "Bluser",
    "/collections/hoodies": "Hoodies",
    "/collections/sweatshirts": "Sweatshirts",
    "/collections/sneakers": "Sneakers",
    "/collections/sandaler": "Sandaler",
    "/collections/stoevler": "Støvler",
    "/collections/sko": "Sko",
    "/collections/tasker": "Tasker",
    "/collections/toerklaeder": "Tørklæder",
    "/collections/baelter": "Bælter",
    "/collections/hatte": "Hatte",
    "/collections/solbriller": "Solbriller",
    "/collections/parfume": "Parfume",
    # Also match product page URLs
    "/products/": "_product_page",
}


def _url_to_product_type(url: str) -> Optional[str]:
    """
    Map a landing page URL to a product type.

    Handles:
    - Collection pages: /collections/bukser → "Bukser"
    - Product pages: /products/acne-studios-wool-trouser-black → "Bukser" (via keyword in URL)
    """
    url_lower = url.lower()

    # Direct collection match
    for pattern, ptype in _URL_TO_PRODUCT_TYPE.items():
        if pattern == "/products/":
            continue
        if pattern in url_lower:
            return ptype

    # Product page — try to extract type from URL handle
    if "/products/" in url_lower:
        handle = url_lower.split("/products/")[-1].split("?")[0]
        # Check if handle contains type keywords
        type_keywords = {
            "trouser": "Bukser", "pant": "Bukser", "bukser": "Bukser",
            "shirt": "Skjorter", "skjorte": "Skjorter",
            "t-shirt": "T-Shirts", "tee": "T-Shirts",
            "knit": "Strik", "strik": "Strik", "sweater": "Strik",
            "jacket": "Jakker", "jakke": "Jakker", "coat": "Jakker",
            "blazer": "Blazere",
            "dress": "Kjoler", "kjole": "Kjoler",
            "hoodie": "Hoodies",
            "sneaker": "Sneakers",
            "boot": "Støvler", "stoevl": "Støvler",
            "bag": "Tasker", "taske": "Tasker",
            "scarf": "Tørklæder",
            "belt": "Bælter", "baelte": "Bælter",
            "sunglasses": "Solbriller", "solbrille": "Solbriller",
        }
        for keyword, ptype in type_keywords.items():
            if keyword in handle:
                return ptype

    return None


# ═══════════════════════════════════════════════
# Search Console API
# ═══════════════════════════════════════════════

async def _refresh_access_token(
    config: SearchConsoleConfig,
    db: AsyncSession,
) -> Optional[str]:
    """
    Refresh the Google OAuth2 access token if expired.

    Returns the current valid access token, or None if refresh fails.
    """
    from app.core.security import decrypt_token, encrypt_token

    # Check if current token is still valid
    if config.token_expiry and config.token_expiry > datetime.now(timezone.utc):
        return decrypt_token(config.access_token_encrypted)

    # Need to refresh
    if not config.refresh_token_encrypted:
        logger.error(f"No refresh token stored for org {config.organisation_id}")
        return None
    refresh_token = decrypt_token(config.refresh_token_encrypted)
    if not refresh_token:
        logger.error(f"No refresh token for org {config.organisation_id}")
        return None

    # Google OAuth2 token refresh endpoint
    from app.core.config import get_settings
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

            new_access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)

            config.access_token_encrypted = encrypt_token(new_access_token)
            config.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            await db.commit()

            return new_access_token

        except Exception as e:
            logger.error(f"Token refresh failed for org {config.organisation_id}: {e}")
            return None


async def _fetch_search_analytics(
    property_url: str,
    access_token: str,
    days: int = 28,
    row_limit: int = 500,
) -> list[dict]:
    """
    Fetch search analytics from Google Search Console API.

    Returns rows of: query, page, clicks, impressions, ctr, position.
    """
    end_date = datetime.now(timezone.utc).date() - timedelta(days=3)  # 3-day delay for SC data
    start_date = end_date - timedelta(days=days)

    payload = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": row_limit,
        "dataState": "final",
    }

    url = (
        f"https://www.googleapis.com/webmasters/v3/sites/"
        f"{quote(property_url, safe='')}/searchAnalytics/query"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    rows = []
    for row in data.get("rows", []):
        keys = row.get("keys", [])
        if len(keys) >= 2:
            rows.append({
                "query": keys[0],
                "page": keys[1],
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": row.get("ctr", 0),
                "position": row.get("position", 0),
            })

    return rows


# ═══════════════════════════════════════════════
# Sync job
# ═══════════════════════════════════════════════

async def sync_keyword_performance(
    db: AsyncSession,
    organisation_id: uuid.UUID,
) -> dict:
    """
    Sync keyword performance data from Google Search Console.

    This is the main sync job. Should be called periodically (weekly recommended).

    Flow:
    1. Load SearchConsoleConfig for the organisation
    2. Refresh access token if needed
    3. Fetch search analytics from Search Console API
    4. Map landing pages to product types
    5. Aggregate keywords per product type
    6. Upsert into KeywordPerformance table

    Returns a summary dict with counts.
    """
    # Load config
    result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == organisation_id,
            SearchConsoleConfig.is_active == True,
        )
    )
    config = result.scalar_one_or_none()

    if not config:
        return {"status": "skipped", "reason": "No active Search Console config"}

    if not config.property_url:
        return {"status": "skipped", "reason": "No property URL configured"}

    # Refresh access token
    access_token = await _refresh_access_token(config, db)
    if not access_token:
        return {"status": "error", "reason": "Could not obtain valid access token"}

    # Fetch search analytics
    try:
        rows = await _fetch_search_analytics(
            config.property_url, access_token, days=config.sync_period_days
        )
    except Exception as e:
        logger.error(f"Search Console API error for org {organisation_id}: {e}")
        return {"status": "error", "reason": "Search Console API-fejl — prøv igen senere"}

    if not rows:
        config.last_synced_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "ok", "keywords_synced": 0, "reason": "No search data available"}

    # Map rows to product types and aggregate
    aggregated: dict[str, dict[str, dict]] = {}  # product_type → keyword → metrics

    for row in rows:
        product_type = _url_to_product_type(row["page"])
        if not product_type:
            continue

        keyword = row["query"].strip().lower()
        if not keyword or len(keyword) < 3:
            continue

        # Skip brand-only queries (too generic)
        if len(keyword.split()) == 1:
            continue

        if product_type not in aggregated:
            aggregated[product_type] = {}

        if keyword not in aggregated[product_type]:
            aggregated[product_type][keyword] = {
                "clicks": 0, "impressions": 0,
                "position_sum": 0, "position_count": 0,
                "ctr_sum": 0, "ctr_count": 0,
                "landing_page": row["page"],
            }

        agg = aggregated[product_type][keyword]
        agg["clicks"] += row["clicks"]
        agg["impressions"] += row["impressions"]
        agg["position_sum"] += row["position"]
        agg["position_count"] += 1
        agg["ctr_sum"] += row["ctr"]
        agg["ctr_count"] += 1

    # Delete old data for this organisation (full refresh)
    await db.execute(
        delete(KeywordPerformance).where(
            KeywordPerformance.organisation_id == organisation_id
        )
    )

    # Insert new keyword performance data (top 20 per product type)
    total_inserted = 0
    now = datetime.now(timezone.utc)

    for product_type, keywords in aggregated.items():
        # Sort by clicks descending, take top 20
        sorted_kws = sorted(
            keywords.items(),
            key=lambda x: x[1]["clicks"],
            reverse=True,
        )[:20]

        for keyword, metrics in sorted_kws:
            avg_pos = (
                metrics["position_sum"] / metrics["position_count"]
                if metrics["position_count"] > 0 else 0
            )
            avg_ctr = (
                metrics["ctr_sum"] / metrics["ctr_count"]
                if metrics["ctr_count"] > 0 else 0
            )

            kp = KeywordPerformance(
                organisation_id=organisation_id,
                product_type=product_type,
                keyword=keyword,
                clicks=metrics["clicks"],
                impressions=metrics["impressions"],
                avg_position=round(avg_pos, 1),
                ctr=round(avg_ctr, 4),
                landing_page=metrics["landing_page"],
                sync_period_days=config.sync_period_days,
                last_synced_at=now,
            )
            db.add(kp)
            total_inserted += 1

    # Update last synced timestamp
    config.last_synced_at = now
    await db.commit()

    logger.info(
        f"Search Console sync for org {organisation_id}: "
        f"{total_inserted} keywords across {len(aggregated)} product types"
    )

    return {
        "status": "ok",
        "keywords_synced": total_inserted,
        "product_types": len(aggregated),
        "raw_rows": len(rows),
    }


# ═══════════════════════════════════════════════
# Data retrieval for AI prompt injection (Layer 2)
# ═══════════════════════════════════════════════

async def get_historical_keywords(
    db: AsyncSession,
    organisation_id: uuid.UUID,
    min_clicks: int = 2,
) -> dict[str, list[str]]:
    """
    Get top-performing keywords grouped by product type.

    Used by the import pipeline to inject historical keyword context
    into AI extraction and keyword validation.

    Returns
    -------
    dict[str, list[str]]
        Mapping of product_type → list of top keywords (max 5 per type).
        Only includes keywords with at least min_clicks clicks.
    """
    result = await db.execute(
        select(KeywordPerformance)
        .where(
            KeywordPerformance.organisation_id == organisation_id,
            KeywordPerformance.clicks >= min_clicks,
        )
        .order_by(KeywordPerformance.clicks.desc())
    )
    rows = result.scalars().all()

    grouped: dict[str, list[str]] = {}
    for row in rows:
        if row.product_type not in grouped:
            grouped[row.product_type] = []
        if len(grouped[row.product_type]) < 5:
            grouped[row.product_type].append(row.keyword)

    return grouped


async def get_keyword_stats(
    db: AsyncSession,
    organisation_id: uuid.UUID,
) -> dict:
    """
    Get summary statistics for keyword performance data.

    Used by the admin/settings UI to show Search Console status.
    """
    # Config status
    config_result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == organisation_id
        )
    )
    config = config_result.scalar_one_or_none()

    # Keyword counts
    from sqlalchemy import func as sa_func
    count_result = await db.execute(
        select(sa_func.count(KeywordPerformance.id)).where(
            KeywordPerformance.organisation_id == organisation_id
        )
    )
    total_keywords = count_result.scalar() or 0

    type_result = await db.execute(
        select(sa_func.count(sa_func.distinct(KeywordPerformance.product_type))).where(
            KeywordPerformance.organisation_id == organisation_id
        )
    )
    total_types = type_result.scalar() or 0

    return {
        "configured": config is not None,
        "active": config.is_active if config else False,
        "property_url": config.property_url if config else None,
        "last_synced": config.last_synced_at.isoformat() if config and config.last_synced_at else None,
        "total_keywords": total_keywords,
        "product_types_covered": total_types,
    }
