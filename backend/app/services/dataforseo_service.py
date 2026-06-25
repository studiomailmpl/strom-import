"""
DataForSEO Service — Layer 2 keyword intelligence.

Enriches validated keywords with real market data from DataForSEO:
  - Monthly search volume (exact, Danish market)
  - Keyword difficulty (0-100)
  - CPC (cost per click — signals commercial intent)
  - Related keywords with volumes (for discovery)

Called AFTER Autocomplete validation (Layer 1) and BEFORE Search Console
boost (Layer 3). Only keywords that survived Layer 1 get looked up here,
keeping costs minimal.

Pricing: ~$0.10 per 1,000 keywords (google.dk, Danish).
Typical import: 30 products × 3 keywords = 90 lookups ≈ $0.009.
"""

import asyncio
import base64
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════

@dataclass
class KeywordData:
    """Enriched keyword data from DataForSEO."""
    keyword: str
    search_volume: int = 0           # Monthly searches (Danish market)
    keyword_difficulty: int = 0       # 0-100, lower = easier to rank
    cpc: float = 0.0                 # Cost per click in DKK (commercial intent signal)
    competition: float = 0.0          # 0-1, Google Ads competition level
    trend: list[int] = field(default_factory=list)  # Last 12 months volume trend
    related_keywords: list["KeywordData"] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "keyword": self.keyword,
            "search_volume": self.search_volume,
            "keyword_difficulty": self.keyword_difficulty,
            "cpc": round(self.cpc, 2),
            "competition": round(self.competition, 3),
        }


# ═══════════════════════════════════════════════
# Cache — avoid re-fetching keywords within same session
# ═══════════════════════════════════════════════

_volume_cache: dict[str, tuple[float, KeywordData]] = {}
_volume_cache_lock = threading.Lock()
_CACHE_TTL = 604800  # 7 days — search volumes change slowly, saves API costs


def _cache_key(keyword: str) -> str:
    return hashlib.md5(f"da:dk:{keyword.lower().strip()}".encode()).hexdigest()


def _get_cached(keyword: str) -> Optional[KeywordData]:
    key = _cache_key(keyword)
    with _volume_cache_lock:
        if key in _volume_cache:
            ts, data = _volume_cache[key]
            if time.time() - ts < _CACHE_TTL:
                return data
            del _volume_cache[key]
    return None


def _set_cached(keyword: str, data: KeywordData) -> None:
    key = _cache_key(keyword)
    with _volume_cache_lock:
        _volume_cache[key] = (time.time(), data)
        # Evict old entries
        if len(_volume_cache) > 10000:
            sorted_keys = sorted(_volume_cache, key=lambda k: _volume_cache[k][0])
            for old in sorted_keys[:2000]:
                del _volume_cache[old]


# ═══════════════════════════════════════════════
# DataForSEO API client
# ═══════════════════════════════════════════════

def _get_auth_header(login: str | None = None, password: str | None = None) -> str:
    """Build Basic Auth header for DataForSEO."""
    if not login or not password:
        settings = get_settings()
        login = settings.dataforseo_login
        password = settings.dataforseo_password
    credentials = f"{login}:{password}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def is_configured() -> bool:
    """Check if DataForSEO credentials are set (env-level)."""
    settings = get_settings()
    return bool(settings.dataforseo_login and settings.dataforseo_password)


async def get_org_credentials(db, organisation_id) -> tuple[str, str] | None:
    """
    Get DataForSEO credentials for an organisation from the database.
    Returns (login, password) tuple or None if not configured.
    """
    from sqlalchemy import select
    from app.models.keyword_performance import SearchConsoleConfig
    from app.core.security import decrypt_token

    try:
        result = await db.execute(
            select(SearchConsoleConfig).where(
                SearchConsoleConfig.organisation_id == organisation_id
            )
        )
        config = result.scalar_one_or_none()
        if config and config.dataforseo_login_encrypted and config.dataforseo_password_encrypted:
            login = decrypt_token(config.dataforseo_login_encrypted)
            password = decrypt_token(config.dataforseo_password_encrypted)
            return (login, password)
    except Exception as e:
        logger.warning(f"Failed to get org DataForSEO credentials: {e}")
    return None


def is_configured_for_org(org_credentials: tuple[str, str] | None) -> bool:
    """Check if DataForSEO is available (either env or org-level)."""
    if org_credentials:
        return True
    return is_configured()


async def get_keyword_volumes(
    keywords: list[str],
    language_code: str = "da",
    location_code: int = 2208,  # Denmark
    org_credentials: tuple[str, str] | None = None,
) -> dict[str, KeywordData]:
    """
    Fetch search volume + difficulty for a batch of keywords.

    Uses DataForSEO's Google Keyword Data API (Keywords Data → Google → Search Volume).
    Batches up to 700 keywords per request (API limit is 700).

    Parameters
    ----------
    keywords : list[str]
        Keywords to look up (max ~700 per call).
    language_code : str
        Language for results. Default: "da" (Danish).
    location_code : int
        Google location code. Default: 2208 (Denmark).

    Returns
    -------
    dict[str, KeywordData]
        Mapping of keyword → enriched data. Missing keywords get zero values.
    """
    if not is_configured_for_org(org_credentials):
        logger.warning("DataForSEO not configured — skipping keyword enrichment")
        return {kw: KeywordData(keyword=kw) for kw in keywords}

    auth_login = org_credentials[0] if org_credentials else None
    auth_password = org_credentials[1] if org_credentials else None

    # Check cache first, only fetch uncached keywords
    results: dict[str, KeywordData] = {}
    to_fetch: list[str] = []

    for kw in keywords:
        cached = _get_cached(kw)
        if cached:
            results[kw] = cached
        else:
            to_fetch.append(kw)

    if not to_fetch:
        logger.debug(f"All {len(keywords)} keywords served from cache")
        return results

    logger.info(f"Fetching volume data for {len(to_fetch)} keywords from DataForSEO ({len(results)} cached)")

    # DataForSEO endpoint: Keywords Data → Google → Search Volume (Live)
    url = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"

    payload = [{
        "keywords": to_fetch[:700],  # API limit
        "language_code": language_code,
        "location_code": location_code,
        # No date_from — uses latest data by default
    }]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": _get_auth_header(auth_login, auth_password),
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        # Parse response
        if data.get("status_code") != 20000:
            logger.error(f"DataForSEO API error: {data.get('status_message', 'Unknown')}")
            # Return empty data for all keywords — don't block the pipeline
            for kw in to_fetch:
                results[kw.lower()] = KeywordData(keyword=kw)
            return results

        # Extract results from nested structure
        tasks = data.get("tasks", [])
        for task in tasks:
            if task.get("status_code") != 20000:
                continue
            task_results = task.get("result", [])
            for item in task_results:
                if not item:
                    continue
                kw_text = (item.get("keyword") or "").strip()
                if not kw_text:
                    continue

                kw_data = KeywordData(
                    keyword=kw_text,
                    search_volume=item.get("search_volume") or 0,
                    keyword_difficulty=item.get("keyword_difficulty") or 0,
                    cpc=item.get("cpc") or 0.0,
                    competition=item.get("competition") or 0.0,
                )

                # Extract monthly trend if available
                monthly = item.get("monthly_searches") or []
                if monthly:
                    kw_data.trend = [m.get("search_volume", 0) for m in monthly[:12]]

                results[kw_text.lower()] = kw_data
                _set_cached(kw_text, kw_data)

        # Fill in any keywords that weren't in the response (always use lowercase keys)
        for kw in to_fetch:
            kw_lower = kw.lower()
            if kw_lower not in results:
                empty = KeywordData(keyword=kw, search_volume=0)
                results[kw_lower] = empty
                _set_cached(kw, empty)

        logger.info(
            f"DataForSEO: {len(to_fetch)} keywords fetched, "
            f"avg volume={sum(r.search_volume for r in results.values()) / max(len(results), 1):.0f}"
        )

    except httpx.HTTPStatusError as e:
        logger.error(f"DataForSEO HTTP error: {e.response.status_code} — {e.response.text[:200]}")
        for kw in to_fetch:
            results[kw.lower()] = KeywordData(keyword=kw)
    except Exception as e:
        logger.error(f"DataForSEO request failed: {e}")
        for kw in to_fetch:
            results[kw.lower()] = KeywordData(keyword=kw)

    return results


async def get_related_keywords(
    keyword: str,
    language_code: str = "da",
    location_code: int = 2208,
    limit: int = 10,
    org_credentials: tuple[str, str] | None = None,
) -> list[KeywordData]:
    """
    Fetch related keywords with volumes for discovery.

    Uses DataForSEO's Keywords for Keywords endpoint to find
    semantically related keywords the AI might not have considered.

    Parameters
    ----------
    keyword : str
        Seed keyword to find related terms for.
    limit : int
        Max related keywords to return.

    Returns
    -------
    list[KeywordData]
        Related keywords sorted by search volume (descending).
    """
    if not is_configured_for_org(org_credentials):
        return []

    auth_login = org_credentials[0] if org_credentials else None
    auth_password = org_credentials[1] if org_credentials else None

    url = "https://api.dataforseo.com/v3/keywords_data/google_ads/keywords_for_keywords/live"

    payload = [{
        "keywords": [keyword],
        "language_code": language_code,
        "location_code": location_code,
        "sort_by": "search_volume",
        "limit": limit,
    }]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": _get_auth_header(auth_login, auth_password),
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        related: list[KeywordData] = []

        tasks = data.get("tasks", [])
        for task in tasks:
            if task.get("status_code") != 20000:
                continue
            # DataForSEO keywords_for_keywords returns:
            # result[0].items[] — each item is a keyword with metrics
            task_results = task.get("result", [])
            for result_obj in task_results:
                if not result_obj:
                    continue
                # The actual keywords are inside .items[]
                items = result_obj.get("items") if isinstance(result_obj, dict) else None
                if items is None:
                    # Fallback: maybe the API returns flat items (legacy)
                    items = [result_obj]
                for item in items:
                    if not item:
                        continue
                    kw_text = (item.get("keyword") or "").strip()
                    if not kw_text or kw_text.lower() == keyword.lower():
                        continue
                    related.append(KeywordData(
                        keyword=kw_text,
                        search_volume=item.get("search_volume") or 0,
                        keyword_difficulty=item.get("keyword_difficulty") or 0,
                        cpc=item.get("cpc") or 0.0,
                        competition=item.get("competition") or 0.0,
                    ))

        # Sort by volume descending
        related.sort(key=lambda k: k.search_volume, reverse=True)
        return related[:limit]

    except Exception as e:
        logger.warning(f"DataForSEO related keywords failed for '{keyword}': {e}")
        return []


# ═══════════════════════════════════════════════
# High-level enrichment for the keyword pipeline
# ═══════════════════════════════════════════════

async def enrich_keywords(
    keywords: list[str],
    product_type: str = "",
    min_volume: int = 0,
    discover_alternatives: bool = True,
    org_credentials: tuple[str, str] | None = None,
) -> list[dict]:
    """
    Enrich a list of keywords with volume + difficulty data,
    optionally discovering better alternatives.

    This is the main entry point called from seo_keyword_service.

    Parameters
    ----------
    keywords : list[str]
        Keywords to enrich (already validated by Autocomplete).
    product_type : str
        Product type for context in related keyword filtering.
    min_volume : int
        Drop keywords below this volume. 0 = keep all.
    discover_alternatives : bool
        If True, fetch related keywords for the top candidate
        and suggest higher-volume alternatives.

    Returns
    -------
    list[dict]
        Enriched keyword dicts with: keyword, search_volume,
        keyword_difficulty, cpc, competition, source.
        Sorted by a composite score (volume × achievability).
    """
    if not is_configured_for_org(org_credentials) or not keywords:
        # Return keywords as-is with zero metrics
        return [
            {"keyword": kw, "search_volume": 0, "keyword_difficulty": 0,
             "cpc": 0.0, "competition": 0.0, "source": "ai"}
            for kw in keywords
        ]

    # Step 1: Get volumes for existing keywords
    volume_data = await get_keyword_volumes(keywords, org_credentials=org_credentials)

    # Step 2: Optionally discover related keywords
    alternatives: list[KeywordData] = []
    if discover_alternatives and keywords:
        # Use the first keyword as seed for related discovery
        # Only if at least one keyword has low volume
        low_volume = any(
            volume_data.get(kw, KeywordData(keyword=kw)).search_volume < 50
            for kw in keywords
        )
        if low_volume:
            alt_results = await get_related_keywords(keywords[0], limit=8, org_credentials=org_credentials)
            # Filter: only keep alternatives relevant to product type
            if product_type:
                pt_lower = product_type.lower()
                alt_results = [
                    a for a in alt_results
                    if pt_lower in a.keyword.lower() or a.search_volume > 200
                ]
            alternatives = alt_results

    # Step 3: Build enriched list
    enriched: list[dict] = []
    seen: set[str] = set()

    # Original keywords first
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if kw_lower in seen:
            continue
        seen.add(kw_lower)

        data = volume_data.get(kw_lower) or volume_data.get(kw) or KeywordData(keyword=kw)
        enriched.append({
            "keyword": kw,
            "search_volume": data.search_volume,
            "keyword_difficulty": data.keyword_difficulty,
            "cpc": round(data.cpc, 2),
            "competition": round(data.competition, 3),
            "source": "ai",
        })

    # Alternatives (marked as discovered)
    for alt in alternatives:
        alt_lower = alt.keyword.lower().strip()
        if alt_lower in seen:
            continue
        seen.add(alt_lower)

        if alt.search_volume >= min_volume:
            enriched.append({
                "keyword": alt.keyword,
                "search_volume": alt.search_volume,
                "keyword_difficulty": alt.keyword_difficulty,
                "cpc": round(alt.cpc, 2),
                "competition": round(alt.competition, 3),
                "source": "discovered",
            })

    # Step 4: Score and sort
    # Composite score: high volume + low difficulty = best
    # Formula: volume_score × achievability_score
    for item in enriched:
        vol = item["search_volume"]
        diff = item["keyword_difficulty"]
        # Volume score: log-scale so 10 vs 100 matters more than 1000 vs 10000
        vol_score = min(100, (vol ** 0.5) * 3) if vol > 0 else 0
        # Achievability: inverse of difficulty
        achieve_score = max(0, 100 - diff)
        item["_score"] = vol_score * achieve_score / 100

    enriched.sort(key=lambda x: x["_score"], reverse=True)

    # Remove internal score
    for item in enriched:
        del item["_score"]

    # Drop zero-volume if min_volume > 0
    if min_volume > 0:
        enriched = [e for e in enriched if e["search_volume"] >= min_volume or e["source"] == "ai"]

    logger.info(
        f"DataForSEO enrichment: {len(keywords)} input → {len(enriched)} output, "
        f"volumes: {[e['search_volume'] for e in enriched[:5]]}"
    )

    return enriched
