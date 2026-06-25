"""
SEO routes — Search Console OAuth, DataForSEO config, sync trigger, keyword stats.

Endpoints:
  GET  /seo/status             — Get Search Console config status + keyword stats
  GET  /seo/connect            — Start Google OAuth flow for Search Console
  GET  /seo/callback           — Google OAuth callback (exchanges code for tokens)
  GET  /seo/config             — Get current Search Console configuration
  PUT  /seo/config             — Create/update Search Console configuration
  GET  /seo/dataforseo         — Get DataForSEO configuration status
  PUT  /seo/dataforseo         — Save DataForSEO credentials
  DELETE /seo/dataforseo       — Remove DataForSEO credentials
  POST /seo/sync               — Trigger manual keyword sync from Search Console
  GET  /seo/keywords           — Get top-performing keywords (grouped by product type)
  GET  /seo/keywords/:type     — Get keywords for a specific product type
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete as sa_delete

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.keyword_performance import KeywordPerformance, SearchConsoleConfig
from app.models.oauth_nonce import OAuthNonce, NONCE_TTL

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════

class SEOStatusResponse(BaseModel):
    configured: bool
    active: bool
    property_url: str | None = None
    last_synced: str | None = None
    total_keywords: int = 0
    product_types_covered: int = 0
    # DataForSEO (Layer 2)
    dataforseo_configured: bool = False


class SearchConsoleConfigUpdate(BaseModel):
    property_url: str = Field(..., min_length=1, max_length=512)
    is_active: bool = True
    sync_period_days: int = Field(28, ge=7, le=90)
    # OAuth tokens — encrypted before storage
    access_token: str | None = None
    refresh_token: str | None = None


class SearchConsoleConfigResponse(BaseModel):
    property_url: str
    is_active: bool
    sync_period_days: int
    last_synced: str | None = None
    has_tokens: bool = False


class KeywordResponse(BaseModel):
    keyword: str
    product_type: str
    clicks: int
    impressions: int
    avg_position: float
    ctr: float
    landing_page: str = ""
    last_synced: str | None = None


class SyncResponse(BaseModel):
    status: str
    keywords_synced: int = 0
    product_types: int = 0
    raw_rows: int = 0
    reason: str | None = None


class DataForSEOConfigUpdate(BaseModel):
    login: str = Field(..., min_length=1, max_length=512)
    password: str = Field(..., min_length=1, max_length=512)


class DataForSEOConfigResponse(BaseModel):
    configured: bool
    login_hint: str | None = None  # e.g. "mal***@gmail.com"


# Google OAuth 2.0 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


# ═══════════════════════════════════════════════
# OAuth Flow Endpoints
# ═══════════════════════════════════════════════

@router.get("/seo/connect")
async def start_google_oauth(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start the Google OAuth2 flow for Search Console access.

    Returns a redirect URL that the frontend should open in a new window.
    The user authorises access, Google redirects back to /seo/callback.
    """
    settings = get_settings()

    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )

    # Cleanup expired nonces
    cutoff = datetime.now(timezone.utc) - NONCE_TTL
    await db.execute(sa_delete(OAuthNonce).where(OAuthNonce.created_at < cutoff))

    # Generate state token and persist in DB (survives server restarts)
    state = secrets.token_urlsafe(32)
    db.add(OAuthNonce(
        nonce=state,
        shop_domain="google_search_console",  # reuse field to identify flow type
        user_id=str(user.id) if hasattr(user, "id") else "unknown",
        org_id=str(user.organisation_id),
    ))
    await db.commit()

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": SEARCH_CONSOLE_SCOPE,
        "access_type": "offline",       # get refresh_token
        "prompt": "consent",            # force consent to always get refresh_token
        "state": state,
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/seo/callback")
async def google_oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Google OAuth2 callback — exchanges authorization code for tokens.

    After success, stores encrypted tokens in SearchConsoleConfig and
    redirects the user back to the frontend settings page.
    """
    logger.info("=== OAUTH CALLBACK HIT === code=%s, state=%s, error=%s", bool(code), state[:20] if state else None, error)
    settings = get_settings()
    frontend_url = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"

    # Handle error from Google
    if error:
        logger.warning("Google OAuth error: %s", error)
        return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_error={error}")

    if not code or not state:
        return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_error=missing_params")

    # Validate state token from database (survives server restarts)
    result = await db.execute(
        select(OAuthNonce).where(OAuthNonce.nonce == state)
    )
    nonce_row = result.scalar_one_or_none()

    if not nonce_row:
        logger.warning("Invalid OAuth state token (not found in DB): %s", state[:20])
        return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_error=invalid_state")

    if nonce_row.is_expired:
        logger.warning("Expired OAuth state token (nonce=%s)", state[:20])
        await db.execute(sa_delete(OAuthNonce).where(OAuthNonce.nonce == state))
        await db.commit()
        return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_error=expired_state")

    organisation_id = nonce_row.org_id

    # Delete the used nonce (one-time use)
    await db.execute(sa_delete(OAuthNonce).where(OAuthNonce.nonce == state))
    await db.flush()

    # Wrap entire token exchange + save in try/except to catch ALL errors
    try:
        # Exchange authorization code for tokens
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                token_resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "redirect_uri": settings.google_redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                token_resp.raise_for_status()
                tokens = token_resp.json()
        except httpx.HTTPError as e:
            # Extract the actual error from Google's response
            error_detail = "unknown"
            try:
                error_body = token_resp.json()
                error_detail = error_body.get("error", "unknown")
                error_desc = error_body.get("error_description", "")
                logger.error(
                    "Google token exchange failed: %s — error=%s, desc=%s, status=%s",
                    e, error_detail, error_desc, token_resp.status_code,
                )
            except Exception:
                logger.error("Google token exchange failed: %s", e)
            return RedirectResponse(
                f"{frontend_url}/dashboard/settings?seo_error=token_exchange_failed_{error_detail}"
            )

        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)

        logger.info("Google token exchange OK: has_access=%s, has_refresh=%s", bool(access_token), bool(refresh_token))

        if not access_token:
            logger.error("No access_token in Google response (keys: %s)", list(tokens.keys()))
            return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_error=no_access_token")

        # Fetch the user's Search Console properties to auto-detect site URL
        property_url = ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                sc_resp = await client.get(
                    "https://www.googleapis.com/webmasters/v3/sites",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if sc_resp.status_code == 200:
                    sites = sc_resp.json().get("siteEntry", [])
                    if sites:
                        # Pick the first verified site
                        property_url = sites[0].get("siteUrl", "")
                        logger.info(
                            "Auto-detected Search Console property: %s (from %d sites)",
                            property_url, len(sites),
                        )
        except Exception as e:
            logger.warning("Could not auto-detect Search Console property: %s", e)

        # Store tokens encrypted in SearchConsoleConfig
        from app.core.security import encrypt_token
        import uuid

        org_uuid = uuid.UUID(organisation_id)
        result = await db.execute(
            select(SearchConsoleConfig).where(
                SearchConsoleConfig.organisation_id == org_uuid
            )
        )
        config = result.scalar_one_or_none()

        if not config:
            config = SearchConsoleConfig(
                organisation_id=org_uuid,
            )
            db.add(config)

        config.access_token_encrypted = encrypt_token(access_token)
        if refresh_token:
            config.refresh_token_encrypted = encrypt_token(refresh_token)
        config.token_expiry = datetime.now(timezone.utc).replace(
            second=0, microsecond=0
        ) + timedelta(seconds=expires_in)
        config.is_active = True
        if property_url:
            config.property_url = property_url

        await db.commit()

        logger.info(
            "Search Console connected for org=%s, property=%s",
            organisation_id[:8], property_url or "(manual)",
        )

        return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_connected=true")

    except Exception as e:
        logger.error("Unexpected error in OAuth callback: %s", e, exc_info=True)
        await db.rollback()
        return RedirectResponse(f"{frontend_url}/dashboard/settings?seo_error=callback_error")


# ═══════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════

@router.get("/seo/status", response_model=SEOStatusResponse)
async def get_seo_status(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get Search Console configuration status, keyword stats, and DataForSEO status."""
    from app.services.search_console_service import get_keyword_stats
    from app.services.dataforseo_service import is_configured as dfs_env_configured

    stats = await get_keyword_stats(db, user.organisation_id)

    # Check org-level DataForSEO credentials (takes precedence over .env)
    dfs_configured = dfs_env_configured()
    if not dfs_configured:
        result = await db.execute(
            select(SearchConsoleConfig).where(
                SearchConsoleConfig.organisation_id == user.organisation_id
            )
        )
        config = result.scalar_one_or_none()
        if config and config.dataforseo_login_encrypted and config.dataforseo_password_encrypted:
            dfs_configured = True

    return SEOStatusResponse(**stats, dataforseo_configured=dfs_configured)


@router.get("/seo/config", response_model=SearchConsoleConfigResponse)
async def get_search_console_config(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current Search Console configuration."""
    result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == user.organisation_id
        )
    )
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Search Console not configured")

    return SearchConsoleConfigResponse(
        property_url=config.property_url,
        is_active=config.is_active,
        sync_period_days=config.sync_period_days,
        last_synced=config.last_synced_at.isoformat() if config.last_synced_at else None,
        has_tokens=bool(config.refresh_token_encrypted),
    )


@router.put("/seo/config", response_model=SearchConsoleConfigResponse)
async def update_search_console_config(
    body: SearchConsoleConfigUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update Search Console configuration."""
    result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == user.organisation_id
        )
    )
    config = result.scalar_one_or_none()

    if not config:
        config = SearchConsoleConfig(
            organisation_id=user.organisation_id,
        )
        db.add(config)

    config.property_url = body.property_url
    config.is_active = body.is_active
    config.sync_period_days = body.sync_period_days

    # Encrypt tokens if provided
    if body.access_token or body.refresh_token:
        from app.core.security import encrypt_token
        if body.access_token:
            config.access_token_encrypted = encrypt_token(body.access_token)
        if body.refresh_token:
            config.refresh_token_encrypted = encrypt_token(body.refresh_token)

    await db.commit()
    await db.refresh(config)

    return SearchConsoleConfigResponse(
        property_url=config.property_url,
        is_active=config.is_active,
        sync_period_days=config.sync_period_days,
        last_synced=config.last_synced_at.isoformat() if config.last_synced_at else None,
        has_tokens=bool(config.refresh_token_encrypted),
    )


@router.get("/seo/dataforseo", response_model=DataForSEOConfigResponse)
async def get_dataforseo_config(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get DataForSEO configuration status for this organisation."""
    from app.core.security import decrypt_token

    result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == user.organisation_id
        )
    )
    config = result.scalar_one_or_none()

    if not config or not config.dataforseo_login_encrypted:
        # Fall back to .env check
        from app.services.dataforseo_service import is_configured as dfs_env_configured
        settings = get_settings()
        if dfs_env_configured():
            login = settings.dataforseo_login
            return DataForSEOConfigResponse(
                configured=True,
                login_hint=_mask_email(login),
            )
        return DataForSEOConfigResponse(configured=False)

    try:
        login = decrypt_token(config.dataforseo_login_encrypted)
        return DataForSEOConfigResponse(
            configured=True,
            login_hint=_mask_email(login),
        )
    except Exception:
        return DataForSEOConfigResponse(configured=False)


@router.put("/seo/dataforseo", response_model=DataForSEOConfigResponse)
async def update_dataforseo_config(
    body: DataForSEOConfigUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save DataForSEO API credentials (encrypted)."""
    from app.core.security import encrypt_token

    # Validate credentials using the free user_data endpoint (no module access required)
    import base64
    credentials = f"{body.login}:{body.password}"
    encoded = base64.b64encode(credentials.encode()).decode()
    auth_header = f"Basic {encoded}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            test_resp = await client.get(
                "https://api.dataforseo.com/v3/appendix/user_data",
                headers={
                    "Authorization": auth_header,
                },
            )
            if test_resp.status_code in (401, 403):
                raise HTTPException(
                    status_code=400,
                    detail="Ugyldige DataForSEO-credentials — tjek login og password",
                )
            test_resp.raise_for_status()
            data = test_resp.json()
            if data.get("status_code") != 20000:
                raise HTTPException(
                    status_code=400,
                    detail=f"DataForSEO API-fejl: {data.get('status_message', 'Ukendt fejl')}",
                )
            # Check that account has positive balance
            tasks = data.get("tasks", [])
            if tasks:
                user_data = (tasks[0].get("result") or [{}])
                if isinstance(user_data, list) and user_data:
                    money = user_data[0].get("money", {})
                    balance = money.get("balance", 0) if isinstance(money, dict) else 0
                    if balance <= 0:
                        logger.warning("DataForSEO account has zero balance for org=%s", str(user.organisation_id)[:8])
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Kunne ikke kontakte DataForSEO: {e}")

    # Store encrypted credentials
    result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == user.organisation_id
        )
    )
    config = result.scalar_one_or_none()

    if not config:
        config = SearchConsoleConfig(
            organisation_id=user.organisation_id,
        )
        db.add(config)

    config.dataforseo_login_encrypted = encrypt_token(body.login)
    config.dataforseo_password_encrypted = encrypt_token(body.password)

    await db.commit()

    logger.info("DataForSEO credentials saved for org=%s", str(user.organisation_id)[:8])

    return DataForSEOConfigResponse(
        configured=True,
        login_hint=_mask_email(body.login),
    )


@router.delete("/seo/dataforseo")
async def delete_dataforseo_config(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove DataForSEO credentials for this organisation."""
    result = await db.execute(
        select(SearchConsoleConfig).where(
            SearchConsoleConfig.organisation_id == user.organisation_id
        )
    )
    config = result.scalar_one_or_none()

    if config:
        config.dataforseo_login_encrypted = ""
        config.dataforseo_password_encrypted = ""
        await db.commit()

    return {"status": "ok"}


def _mask_email(email: str) -> str:
    """Mask email for display: mal***@gmail.com"""
    if "@" in email:
        local, domain = email.split("@", 1)
        if len(local) <= 3:
            return f"{local[0]}***@{domain}"
        return f"{local[:3]}***@{domain}"
    if len(email) <= 4:
        return f"{email[0]}***"
    return f"{email[:4]}***"


@router.post("/seo/sync", response_model=SyncResponse)
async def trigger_seo_sync(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a manual keyword sync from Google Search Console.

    Fetches search analytics data, maps to product types, and
    stores top-performing keywords for AI prompt injection.
    """
    from app.services.search_console_service import sync_keyword_performance

    result = await sync_keyword_performance(db, user.organisation_id)
    await db.commit()

    return SyncResponse(**result)


@router.get("/seo/keywords")
async def get_keywords(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Get top-performing keywords grouped by product type (paginated).

    Returns the data that gets injected into AI prompts.
    """
    from app.services.search_console_service import get_historical_keywords

    keywords = await get_historical_keywords(db, user.organisation_id, min_clicks=1)

    # Also fetch full details for the UI (paginated)
    result = await db.execute(
        select(KeywordPerformance)
        .where(KeywordPerformance.organisation_id == user.organisation_id)
        .order_by(KeywordPerformance.product_type, KeywordPerformance.clicks.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = result.scalars().all()

    return {
        "summary": keywords,
        "limit": limit,
        "offset": offset,
        "details": [
            {
                "keyword": r.keyword,
                "product_type": r.product_type,
                "clicks": r.clicks,
                "impressions": r.impressions,
                "avg_position": r.avg_position,
                "ctr": round(r.ctr * 100, 2),
                "landing_page": r.landing_page,
                "last_synced": r.last_synced_at.isoformat() if r.last_synced_at else None,
            }
            for r in rows
        ],
    }


@router.get("/seo/keywords/{product_type}")
async def get_keywords_for_type(
    product_type: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get top-performing keywords for a specific product type."""
    result = await db.execute(
        select(KeywordPerformance)
        .where(
            KeywordPerformance.organisation_id == user.organisation_id,
            KeywordPerformance.product_type == product_type,
        )
        .order_by(KeywordPerformance.clicks.desc())
    )
    rows = result.scalars().all()

    return [
        {
            "keyword": r.keyword,
            "clicks": r.clicks,
            "impressions": r.impressions,
            "avg_position": r.avg_position,
            "ctr": round(r.ctr * 100, 2),
            "landing_page": r.landing_page,
        }
        for r in rows
    ]
