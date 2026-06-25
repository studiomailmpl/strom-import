"""
SEO Keyword Service — three-layer keyword intelligence.

Layer 1: Google Autocomplete validation
  AI generates keyword candidates → validate against Google Autocomplete →
  score by real search behavior → pick best keywords.

Layer 2: DataForSEO keyword enrichment
  Validated keywords get enriched with search volume, difficulty, CPC.
  Zero-volume keywords get replaced with better alternatives.

Layer 3: Search Console feedback loop (see search_console_service.py)
  Historical keyword performance feeds back into AI prompts over time.

This service is called AFTER AI extraction and BEFORE saving to DB,
so every product gets validated keywords before it hits the review screen.
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from typing import Optional
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)


# ════════════��══════════════════════════════════
# In-memory cache for autocomplete results
# ═══════════════════════════════════════════════

# Cache: query_hash → (timestamp, suggestions)
_autocomplete_cache: dict[str, tuple[float, list[str]]] = {}
_autocomplete_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 3600  # 1 hour — autocomplete suggestions change slowly


def _cache_key(query: str, lang: str) -> str:
    """Generate cache key from query + language."""
    raw = f"{lang}:{query.lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(query: str, lang: str) -> Optional[list[str]]:
    """Return cached suggestions if fresh, else None."""
    key = _cache_key(query, lang)
    with _autocomplete_cache_lock:
        if key in _autocomplete_cache:
            ts, suggestions = _autocomplete_cache[key]
            if time.time() - ts < _CACHE_TTL_SECONDS:
                return suggestions
            else:
                del _autocomplete_cache[key]
    return None


def _set_cached(query: str, lang: str, suggestions: list[str]) -> None:
    """Store autocomplete suggestions in cache."""
    key = _cache_key(query, lang)
    with _autocomplete_cache_lock:
        _autocomplete_cache[key] = (time.time(), suggestions)
        # Prevent unbounded growth — evict oldest entries if cache gets large
        if len(_autocomplete_cache) > 5000:
            sorted_keys = sorted(
                _autocomplete_cache.keys(),
                key=lambda k: _autocomplete_cache[k][0],
            )
            for old_key in sorted_keys[:1000]:
                del _autocomplete_cache[old_key]


# ══��════════════════════════���═══════════════════
# Google Autocomplete API
# ═══════════════════════════════════════════════

async def _fetch_autocomplete(
    query: str,
    lang: str = "da",
    country: str = "dk",
    client: Optional[httpx.AsyncClient] = None,
) -> list[str]:
    """
    Fetch Google Autocomplete suggestions for a query.

    Uses the public Google Suggest endpoint (same as the search bar).
    Returns a list of suggestion strings, or empty list on failure.
    """
    # Check cache first
    cached = _get_cached(query, lang)
    if cached is not None:
        return cached

    url = (
        f"https://suggestqueries.google.com/complete/search"
        f"?client=firefox"
        f"&q={quote_plus(query)}"
        f"&hl={lang}"
        f"&gl={country}"
    )

    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=5.0)
        should_close = True

    try:
        response = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; StromImport/1.0)",
        })
        response.raise_for_status()

        # Response format: ["query", ["suggestion1", "suggestion2", ...]]
        data = response.json()
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            suggestions = [s for s in data[1] if isinstance(s, str)]
            _set_cached(query, lang, suggestions)
            return suggestions

        _set_cached(query, lang, [])
        return []

    except Exception as e:
        logger.warning(f"Autocomplete request failed for '{query}': {e}")
        # Cache failures with short TTL (60s) to avoid hammering, but retry sooner
        key = _cache_key(query, lang)
        with _autocomplete_cache_lock:
            _autocomplete_cache[key] = (time.time() - _CACHE_TTL_SECONDS + 60, [])
        return []
    finally:
        if should_close:
            await client.aclose()


# ═══���═══════════════════════════════════════════
# Keyword scoring
# ════════════════════��══════════════════════════

def _score_keyword(
    keyword: str,
    autocomplete_suggestions: list[str],
    product_data: dict,
) -> float:
    """
    Score a keyword candidate based on autocomplete validation and product relevance.

    Scoring factors:
    1. Autocomplete match (0-50 pts): Does the keyword appear in or match autocomplete suggestions?
    2. Specificity bonus (0-20 pts): Does the keyword contain product-specific terms (material, construction)?
    3. Length bonus (0-10 pts): Longer, more specific keywords score higher (within reason)
    4. Relevance penalty (-20 pts): Deduct if keyword doesn't relate to product type/material

    Returns a score from 0-80 (higher = better keyword).
    """
    score = 0.0
    kw_lower = keyword.lower().strip()

    # ── Factor 1: Autocomplete match (0-50 pts) ──
    if autocomplete_suggestions:
        suggestions_lower = [s.lower() for s in autocomplete_suggestions]

        # Exact match: keyword IS one of the suggestions
        if kw_lower in suggestions_lower:
            score += 50
        else:
            # Partial match: keyword is a SUBSTRING of a suggestion
            substring_matches = sum(
                1 for s in suggestions_lower if kw_lower in s
            )
            if substring_matches > 0:
                score += min(35, 15 + substring_matches * 5)
            else:
                # Reverse partial: a suggestion is a substring of the keyword
                reverse_matches = sum(
                    1 for s in suggestions_lower if s in kw_lower
                )
                if reverse_matches > 0:
                    score += min(20, 5 + reverse_matches * 5)

    # ── Factor 2: Specificity bonus (0-20 pts) ──
    # Keywords that contain product-specific terms (material, construction) score higher
    material = (product_data.get("material") or "").lower()
    product_type = (product_data.get("product_type") or "").lower()
    color = (product_data.get("color") or "").lower()

    # Material in keyword
    material_words = set(re.split(r'[,\s]+', material)) - {"", "og", "and", "%"}
    for mw in material_words:
        if len(mw) > 2 and mw in kw_lower:
            score += 10
            break

    # Specific product type detail (not just "bukser" but "cargo bukser")
    if product_type and product_type in kw_lower:
        # Base type is present — check if there's something MORE
        extra_words = kw_lower.replace(product_type, "").strip()
        if extra_words and len(extra_words) > 2:
            score += 10  # More specific than just the type

    # ── Factor 3: Length bonus (0-10 pts) ──
    # Sweet spot: 15-40 chars (specific enough to be useful, not too long)
    kw_len = len(kw_lower)
    if 15 <= kw_len <= 40:
        score += 10
    elif 10 <= kw_len < 15:
        score += 5
    elif kw_len > 40:
        score += 2  # Too long, probably not what people type

    # ��─ Factor 4: Relevance penalty ──
    # Deduct if keyword contains "køb", "online", "tilbud" (non-premium)
    non_premium = {"køb", "online", "tilbud", "billig", "udsalg", "rabat", "gratis"}
    for np_word in non_premium:
        if np_word in kw_lower:
            score -= 20
            break

    return max(0, score)


# ═══════════════���═══════════════════════════════
# Keyword expansion via autocomplete
# ��══════════════════════════════════════════════


# (Removed: _expand_keywords_from_autocomplete — unused dead code)


# ═══════════════════════════════════════════════
# Main public API
# ═══════���════════��══════════════════════════════

async def validate_and_optimize_keywords(
    product: dict,
    historical_keywords: Optional[dict[str, list[str]]] = None,
    org_credentials: tuple[str, str] | None = None,
) -> list[dict]:
    """
    Validate and optimize SEO keywords for a single product.

    This is the main entry point. Called after AI extraction, before DB save.

    Flow:
    1. Take AI-generated keyword candidates
    2. Build additional seed queries from product data
    3. Fetch Google Autocomplete suggestions for all seeds (Layer 1)
    4. Score all candidates (AI + autocomplete-discovered)
    5. Pick top candidates
    6. Enrich with DataForSEO volume + difficulty data (Layer 2)
    7. Apply Search Console historical boost (Layer 3)
    8. Return top 2-3 enriched keywords

    Parameters
    ----------
    product : dict
        Product data with at minimum: seo_keywords, title, vendor,
        product_type, material, color, gender.
    historical_keywords : dict[str, list[str]] | None
        Optional: top-performing keywords from Search Console,
        keyed by product_type. Used to boost known-good keywords.

    Returns
    -------
    list[dict]
        2-3 enriched keyword dicts with keys:
        keyword, search_volume, keyword_difficulty, cpc, competition, source.
        Falls back to list[str] format if DataForSEO is not configured.
    """
    from app.services.dataforseo_service import enrich_keywords, is_configured_for_org

    # Handle both plain strings and enriched dicts from previous runs
    raw_keywords = product.get("seo_keywords") or []
    ai_keywords: list[str] = []
    for kw in raw_keywords:
        if isinstance(kw, dict):
            ai_keywords.append(kw.get("keyword", ""))
        elif isinstance(kw, str):
            ai_keywords.append(kw)
    ai_keywords = [k for k in ai_keywords if k]

    title = product.get("title", "")
    vendor = product.get("vendor", "")
    product_type = product.get("product_type", "")
    material = product.get("material", "")
    color = product.get("color", "")
    gender = product.get("gender", "")

    # ── Step 1: Build seed queries ──
    seed_queries: list[str] = list(ai_keywords)

    type_da = product.get("product_type_da") or product.get("product_type", "")
    if type_da:
        if material:
            main_material = material.split(",")[0].strip().split("%")[-1].strip()
            if main_material and len(main_material) > 2:
                seed_queries.append(f"{main_material} {type_da.lower()}")
        if vendor:
            seed_queries.append(f"{vendor.lower()} {type_da.lower()}")
        gender_da = {"Women": "dame", "Men": "herre", "Unisex": ""}.get(gender, "")
        if gender_da:
            seed_queries.append(f"{type_da.lower()} {gender_da}")

    # Deduplicate seeds
    seen_seeds: set[str] = set()
    unique_seeds: list[str] = []
    for s in seed_queries:
        s_clean = s.lower().strip()
        if s_clean and s_clean not in seen_seeds:
            seen_seeds.add(s_clean)
            unique_seeds.append(s)

    # ── Step 2: Layer 1 — Fetch autocomplete for all seeds ──
    all_candidates: list[tuple[str, list[str]]] = []

    async with httpx.AsyncClient(timeout=5.0) as client:
        autocomplete_results: dict[str, list[str]] = {}

        for i, seed in enumerate(unique_seeds[:8]):
            if i > 0:
                await asyncio.sleep(0.05)
            suggestions = await _fetch_autocomplete(seed, client=client)
            autocomplete_results[seed] = suggestions

        # AI keywords as candidates
        for kw in ai_keywords:
            best_suggestions = autocomplete_results.get(kw, [])
            if not best_suggestions:
                for seed, suggs in autocomplete_results.items():
                    if any(kw.lower() in s.lower() for s in suggs):
                        best_suggestions = suggs
                        break
            all_candidates.append((kw, best_suggestions))

        # Autocomplete-discovered candidates
        product_terms = {
            vendor.lower(), type_da.lower(),
            *[w.lower() for w in material.split(",") if len(w.strip()) > 2],
        }
        product_terms.discard("")

        for seed, suggestions in autocomplete_results.items():
            for s in suggestions:
                s_lower = s.lower()
                if any(term in s_lower for term in product_terms if term):
                    all_candidates.append((s, suggestions))

    # ── Step 3: Add historical keywords as candidates (Layer 3 boost) ──
    if historical_keywords and type_da:
        hist_for_type = historical_keywords.get(type_da, [])
        for hk in hist_for_type[:5]:
            all_candidates.append((hk, [hk]))

    # ── Step 4: Score and rank all candidates ──
    scored: list[tuple[str, float]] = []
    seen_keywords: set[str] = set()

    for keyword, suggestions in all_candidates:
        kw_lower = keyword.lower().strip()
        if kw_lower in seen_keywords:
            continue
        seen_keywords.add(kw_lower)

        score = _score_keyword(keyword, suggestions, product)

        # Historical keyword bonus
        if historical_keywords and type_da:
            hist_for_type = historical_keywords.get(type_da, [])
            if kw_lower in [h.lower() for h in hist_for_type]:
                score += 15

        scored.append((keyword, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # ── Step 5: Pick top candidates (pre-enrichment pool) ──
    # Take more than we need — DataForSEO might reveal that some have zero volume
    pre_pool: list[str] = []
    for kw, score in scored:
        if len(pre_pool) >= 6:  # Take top 6 to give DataForSEO room to optimize
            break
        kw_words = set(kw.lower().split())
        too_similar = False
        for existing in pre_pool:
            existing_words = set(existing.lower().split())
            overlap = len(kw_words & existing_words)
            total = max(len(kw_words), len(existing_words))
            if total > 0 and overlap / total > 0.6:
                too_similar = True
                break
        if not too_similar:
            pre_pool.append(kw)

    # Fill pool if too small
    if len(pre_pool) < 3:
        for kw in ai_keywords:
            if kw not in pre_pool:
                pre_pool.append(kw)
            if len(pre_pool) >= 3:
                break

    # ── Step 6: Layer 2 — DataForSEO enrichment ──
    if is_configured_for_org(org_credentials):
        enriched = await enrich_keywords(
            keywords=pre_pool,
            product_type=type_da,
            min_volume=0,
            discover_alternatives=False,  # Off: saves ~$0.05/product, minimal value for DK market
            org_credentials=org_credentials,
        )

        # Pick top 3 by DataForSEO's composite score (already sorted)
        # But ensure diversity
        final_enriched: list[dict] = []
        for item in enriched:
            if len(final_enriched) >= 3:
                break
            kw_words = set(item["keyword"].lower().split())
            too_similar = False
            for existing in final_enriched:
                existing_words = set(existing["keyword"].lower().split())
                overlap = len(kw_words & existing_words)
                total = max(len(kw_words), len(existing_words))
                if total > 0 and overlap / total > 0.6:
                    too_similar = True
                    break
            if not too_similar:
                final_enriched.append(item)

        # Ensure at least 2
        if len(final_enriched) < 2:
            for item in enriched:
                if item not in final_enriched:
                    final_enriched.append(item)
                if len(final_enriched) >= 2:
                    break

        logger.info(
            f"SEO keywords for '{title}': AI={ai_keywords} → enriched={[e['keyword'] for e in final_enriched]} "
            f"(volumes: {[e['search_volume'] for e in final_enriched]})"
        )

        return final_enriched[:3]

    else:
        # DataForSEO not configured — return plain keywords (backward compatible)
        final_keywords = pre_pool[:3]

        logger.info(
            f"SEO keywords for '{title}': AI={ai_keywords} → validated={final_keywords} "
            f"(DataForSEO not configured, no volume data)"
        )

        return [{"keyword": kw, "search_volume": 0, "keyword_difficulty": 0,
                 "cpc": 0.0, "competition": 0.0, "source": "ai"} for kw in final_keywords]


async def validate_keywords_batch(
    products: list[dict],
    historical_keywords: Optional[dict[str, list[str]]] = None,
    org_credentials: tuple[str, str] | None = None,
) -> list[dict]:
    """
    Validate and optimize SEO keywords for a batch of products.

    Processes products sequentially with small delays to respect
    Google's rate limits. Each product gets 4-8 autocomplete requests,
    so for 10 products that's ~40-80 requests spread over ~4-8 seconds.

    The seo_keywords field on each product will be set to either:
    - list[dict] with {keyword, search_volume, keyword_difficulty, cpc, ...}
      if DataForSEO is configured
    - list[dict] with zero metrics if DataForSEO is not configured

    Parameters
    ----------
    products : list[dict]
        List of product dicts from AI extraction.
    historical_keywords : dict[str, list[str]] | None
        Top-performing keywords from Search Console, keyed by product_type.

    Returns
    -------
    list[dict]
        Same products with updated seo_keywords (enriched format).
    """
    if not products:
        return products

    logger.info(f"Validating SEO keywords for {len(products)} products...")
    start = time.time()

    for i, product in enumerate(products):
        try:
            enriched = await validate_and_optimize_keywords(
                product, historical_keywords=historical_keywords,
                org_credentials=org_credentials,
            )
            product["seo_keywords"] = enriched
        except Exception as e:
            # Non-critical: if validation fails, keep AI keywords as-is
            # but wrap them in the enriched format for consistency
            logger.warning(
                f"Keyword validation failed for '{product.get('title', '?')}': {e}"
            )
            existing = product.get("seo_keywords") or []
            if existing and isinstance(existing[0], str):
                product["seo_keywords"] = [
                    {"keyword": kw, "search_volume": 0, "keyword_difficulty": 0,
                     "cpc": 0.0, "competition": 0.0, "source": "ai"}
                    for kw in existing
                ]

        # Small delay between products to spread autocomplete requests
        if i < len(products) - 1:
            await asyncio.sleep(0.2)

    elapsed = time.time() - start
    logger.info(f"SEO keyword validation completed in {elapsed:.1f}s for {len(products)} products")

    return products
