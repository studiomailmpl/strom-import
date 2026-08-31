"""
Google Drive routes — OAuth connect/callback, status, disconnect, root folder.

Endpoints:
  GET  /drive/connect          — Start Google OAuth flow for Drive (read-only)
  GET  /drive/callback         — OAuth callback, stores encrypted tokens
  GET  /drive/status           — Whether Drive is connected + optional root folder
  POST /drive/disconnect       — Delete the connection
  POST /drive/set-root-folder  — Limit searches to one Drive folder
  POST /drive/index            — Sweep Drive and parse confirmations in the background
  POST /drive/parse/{file_id}  — Parse one file as an order confirmation
  GET  /drive/order-confirmations — List what has been parsed

Follows the same pattern as seo.py: an OAuthNonce row carries CSRF state across
the redirect, and tokens are Fernet-encrypted before they are stored.

─────────────────────────────────────────────────────────────────────────────
SETUP IN GOOGLE CLOUD CONSOLE — required before this flow will work:

  1. Enable the Google Drive API for the project
     (APIs & Services → Library → "Google Drive API" → Enable).
     Without this, the token exchange succeeds but every Drive call returns 403
     with "Google Drive API has not been used in project ... before or it is
     disabled".

  2. Add the callback as an authorised redirect URI on the OAuth 2.0 Client
     (APIs & Services → Credentials → the OAuth client → Authorised redirect
     URIs). Google matches this string exactly, so add every environment:
         http://localhost:8000/api/v1/drive/callback
         https://strom-import-production.up.railway.app/api/v1/drive/callback
     and set GOOGLE_DRIVE_REDIRECT_URI to the matching value per environment.

  3. Add ".../auth/drive.readonly" as a scope on the OAuth consent screen. It is
     a restricted scope: while the app is in Testing mode only listed test users
     can grant it, and publishing requires Google verification.
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import encrypt_token
from app.models.brand import Brand
from app.models.drive_connection import DriveConnection
from app.models.oauth_nonce import NONCE_TTL, OAuthNonce

logger = logging.getLogger(__name__)
router = APIRouter()

# Organisations with an index run in flight. A second run would re-download and
# re-parse the same files, and parsing a PDF costs a Claude Vision call, so the
# endpoint refuses rather than doubling the bill. In-process only: this guards
# the common double-click, not a multi-worker deployment.
_indexing_orgs: set[uuid.UUID] = set()


async def _run_index(org_id: uuid.UUID, vendor_names: list[str], max_files: int) -> None:
    """
    Background sweep: find order confirmations in Drive and parse the ones that
    are new or have changed.

    Opens its own session — the request's session is closed by the time a
    BackgroundTask runs. Every file is handled independently so one unreadable
    document cannot stop the rest.
    """
    from app.core.database import async_session
    from app.services.drive_service import (
        download_file,
        list_order_confirmation_candidates,
    )
    from app.services.order_confirmation_parser import (
        UnsupportedFileType,
        parse_order_confirmation,
    )
    from app.services.order_confirmation_store import (
        get_cached_confirmation,
        save_confirmation,
    )

    settings = get_settings()
    parsed = skipped = failed = 0

    try:
        async with async_session() as db:
            candidates = await list_order_confirmation_candidates(
                db, org_id, vendor_names=vendor_names, max_files=max_files
            )
            # Listing may have refreshed the access token, which only flushes.
            # Commit it now: if every file turns out to be cached below there is
            # no later commit, and the refreshed token would be discarded.
            await db.commit()
            logger.info(
                "Drive index org=%s: %d candidate(s)", str(org_id)[:8], len(candidates)
            )

            for candidate in candidates:
                try:
                    cached = await get_cached_confirmation(
                        db, org_id, candidate.file_id, candidate.modified_time
                    )
                    if cached is not None:
                        skipped += 1
                        continue

                    file_bytes = await download_file(db, org_id, candidate.file_id)
                    if not file_bytes:
                        failed += 1
                        continue

                    result = await parse_order_confirmation(
                        file_bytes,
                        candidate.name,
                        candidate.mime_type,
                        api_key=settings.anthropic_api_key,
                    )
                    if not result.lines:
                        # Nothing to match against later — do not store an empty
                        # parse, so a corrected file gets another chance.
                        logger.info(
                            "Drive index: no lines in %s, not stored", candidate.name
                        )
                        failed += 1
                        continue

                    await save_confirmation(
                        db,
                        org_id,
                        candidate.file_id,
                        candidate.modified_time,
                        candidate.name,
                        result,
                    )
                    await db.commit()
                    parsed += 1

                except UnsupportedFileType:
                    skipped += 1
                except Exception as e:
                    await db.rollback()
                    failed += 1
                    logger.warning(
                        "Drive index: could not parse %s (%s): %s",
                        candidate.name, candidate.file_id, e,
                    )

        logger.info(
            "Drive index finished for org=%s: %d parsed, %d skipped, %d failed",
            str(org_id)[:8], parsed, skipped, failed,
        )
    except Exception as e:
        logger.error("Drive index failed for org=%s: %s", str(org_id)[:8], e, exc_info=True)
    finally:
        _indexing_orgs.discard(org_id)


# Google OAuth 2.0 endpoints — same client as the Search Console flow.
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Written into OAuthNonce.shop_domain to tell this flow apart from the others
# that share the table (Shopify install, Search Console).
NONCE_FLOW = "google_drive"


# ═══════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════

class DriveStatusResponse(BaseModel):
    connected: bool
    root_folder_id: str | None = None
    connected_at: str | None = None


class RootFolderUpdate(BaseModel):
    # Empty string clears the restriction and searches all of Drive.
    root_folder_id: str = Field("", max_length=255)


# ═══════════════════════════════════════════════
# OAuth flow
# ═══════════════════════════════════════════════

@router.get("/drive/connect")
async def start_drive_oauth(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start the Google OAuth2 flow for read-only Drive access.

    Returns the URL the frontend should send the browser to. Google redirects
    back to /drive/callback once the user has authorised.
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

    # Generate state token and persist it (survives server restarts)
    state = secrets.token_urlsafe(32)
    db.add(OAuthNonce(
        nonce=state,
        shop_domain=NONCE_FLOW,
        user_id=str(user.id) if hasattr(user, "id") else "unknown",
        org_id=str(user.organisation_id),
    ))
    await db.commit()

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_drive_redirect_uri,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",   # needed to get a refresh_token
        "prompt": "consent",        # force consent so a refresh_token always comes back
        "state": state,
    }

    return {"auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/drive/callback")
async def drive_oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Google OAuth2 callback — exchanges the authorization code for tokens and
    stores them encrypted, then redirects back to the settings page.
    """
    settings = get_settings()
    frontend_url = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"

    def _fail(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{frontend_url}/dashboard/settings?drive_error={reason}")

    if error:
        logger.warning("Google Drive OAuth error: %s", error)
        return _fail(error)

    if not code or not state:
        return _fail("missing_params")

    # Validate the state token against the database
    result = await db.execute(select(OAuthNonce).where(OAuthNonce.nonce == state))
    nonce_row = result.scalar_one_or_none()

    if not nonce_row:
        logger.warning("Invalid Drive OAuth state token: %s", state[:20])
        return _fail("invalid_state")

    # Reject a nonce minted for a different flow — a Search Console state must
    # not be replayable against the Drive callback.
    if nonce_row.shop_domain != NONCE_FLOW:
        logger.warning("Drive callback got a nonce for flow %r", nonce_row.shop_domain)
        return _fail("invalid_state")

    if nonce_row.is_expired:
        logger.warning("Expired Drive OAuth state token: %s", state[:20])
        await db.execute(sa_delete(OAuthNonce).where(OAuthNonce.nonce == state))
        await db.commit()
        return _fail("expired_state")

    organisation_id = nonce_row.org_id
    connected_by_raw = nonce_row.user_id

    # One-time use
    await db.execute(sa_delete(OAuthNonce).where(OAuthNonce.nonce == state))
    await db.flush()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_drive_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )

        if token_resp.status_code != 200:
            detail = "unknown"
            try:
                body = token_resp.json()
                detail = body.get("error", "unknown")
                logger.error(
                    "Drive token exchange failed: status=%s error=%s desc=%s",
                    token_resp.status_code, detail, body.get("error_description", ""),
                )
            except Exception:
                logger.error("Drive token exchange failed: status=%s", token_resp.status_code)
            return _fail(f"token_exchange_failed_{detail}")

        tokens = token_resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)

        if not access_token:
            logger.error("No access_token in Google response (keys: %s)", list(tokens.keys()))
            return _fail("no_access_token")

        org_uuid = uuid.UUID(organisation_id)
        result = await db.execute(
            select(DriveConnection).where(DriveConnection.organisation_id == org_uuid)
        )
        connection = result.scalar_one_or_none()

        if not connection:
            connection = DriveConnection(organisation_id=org_uuid)
            db.add(connection)

        connection.encrypted_access_token = encrypt_token(access_token)
        # Google only returns a refresh_token on the first consent; on a
        # reconnect without one, keep the token we already hold.
        if refresh_token:
            connection.encrypted_refresh_token = encrypt_token(refresh_token)
        connection.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        try:
            connection.connected_by = uuid.UUID(connected_by_raw)
        except (ValueError, TypeError):
            connection.connected_by = None

        await db.commit()

        logger.info("Google Drive connected for org=%s", organisation_id[:8])
        return RedirectResponse(f"{frontend_url}/dashboard/settings?drive_connected=true")

    except Exception as e:
        logger.error("Unexpected error in Drive OAuth callback: %s", e, exc_info=True)
        await db.rollback()
        return _fail("callback_error")


# ═══════════════════════════════════════════════
# Connection management
# ═══════════════════════════════════════════════

@router.get("/drive/status", response_model=DriveStatusResponse)
async def get_drive_status(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Report whether this organisation has Drive connected."""
    result = await db.execute(
        select(DriveConnection).where(
            DriveConnection.organisation_id == user.organisation_id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection or not connection.encrypted_access_token:
        return DriveStatusResponse(connected=False)

    return DriveStatusResponse(
        connected=True,
        root_folder_id=connection.root_folder_id or None,
        connected_at=connection.connected_at.isoformat() if connection.connected_at else None,
    )


@router.post("/drive/disconnect")
async def disconnect_drive(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete this organisation's Drive connection and its stored tokens."""
    result = await db.execute(
        select(DriveConnection).where(
            DriveConnection.organisation_id == user.organisation_id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        return {"status": "not_connected"}

    await db.delete(connection)
    await db.commit()

    logger.info("Google Drive disconnected for org=%s", str(user.organisation_id)[:8])
    return {"status": "disconnected"}


@router.post("/drive/set-root-folder", response_model=DriveStatusResponse)
async def set_root_folder(
    body: RootFolderUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Limit Drive searches to a single folder. An empty root_folder_id clears the
    restriction so the whole Drive is searched again.
    """
    result = await db.execute(
        select(DriveConnection).where(
            DriveConnection.organisation_id == user.organisation_id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail="Google Drive er ikke forbundet")

    connection.root_folder_id = body.root_folder_id.strip() or None
    await db.commit()

    return DriveStatusResponse(
        connected=True,
        root_folder_id=connection.root_folder_id or None,
        connected_at=connection.connected_at.isoformat() if connection.connected_at else None,
    )


# ═══════════════════════════════════════════════
# Order confirmations
# ═══════════════════════════════════════════════

@router.post("/drive/parse/{file_id}")
async def parse_drive_file(
    file_id: str,
    force: bool = Query(False, description="Re-parse even if the cached parse is current"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Download a Drive file, parse it as an order confirmation, store it and
    return the result.

    A parse is reused when Drive reports the same modifiedTime as the stored
    one — parsing a PDF costs a Claude Vision call. Pass force=true to override.
    """
    from app.services.drive_service import download_file, get_file_metadata
    from app.services.order_confirmation_parser import (
        UnsupportedFileType,
        parse_order_confirmation,
    )
    from app.services.order_confirmation_store import (
        get_cached_confirmation,
        save_confirmation,
        serialise_confirmation,
    )

    metadata = await get_file_metadata(db, user.organisation_id, file_id)
    if metadata is None:
        raise HTTPException(status_code=400, detail="Google Drive er ikke forbundet")

    file_name = metadata.get("name", "")
    mime_type = metadata.get("mimeType", "")
    modified_time = metadata.get("modifiedTime")

    if not force:
        cached = await get_cached_confirmation(
            db, user.organisation_id, file_id, modified_time
        )
        if cached is not None:
            logger.info("Order confirmation %s served from cache", file_id)
            return {"cached": True, **serialise_confirmation(cached)}

    file_bytes = await download_file(db, user.organisation_id, file_id)
    if file_bytes is None:
        raise HTTPException(status_code=400, detail="Google Drive er ikke forbundet")

    settings = get_settings()
    try:
        parsed = await parse_order_confirmation(
            file_bytes,
            file_name,
            mime_type,
            api_key=settings.anthropic_api_key,
        )
    except UnsupportedFileType as e:
        raise HTTPException(status_code=415, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))

    confirmation = await save_confirmation(
        db, user.organisation_id, file_id, modified_time, file_name, parsed
    )
    await db.commit()

    logger.info(
        "Parsed order confirmation %s (%s) — %d line(s) via %s",
        file_name, file_id, len(parsed.lines), parsed.source,
    )
    return {
        "cached": False,
        "source": parsed.source,
        **serialise_confirmation(confirmation),
    }


@router.post("/drive/index")
async def index_order_confirmations(
    background_tasks: BackgroundTasks,
    max_files: int = Query(200, ge=1, le=1000, description="Cap on files parsed per run"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sweep Drive for order confirmations and parse them in the background.

    Importing then matches instantly against confirmations that are already
    parsed, instead of paying for a Drive search, a download and a Claude Vision
    call while the user waits.

    Returns as soon as the sweep is queued. Progress is visible through
    GET /drive/order-confirmations, which lists what has been parsed so far.
    """
    from app.services.drive_service import get_drive_connection

    connection = await get_drive_connection(db, user.organisation_id)
    if connection is None or not connection.encrypted_access_token:
        raise HTTPException(status_code=400, detail="Google Drive er ikke forbundet")

    org_id = user.organisation_id
    if org_id in _indexing_orgs:
        return {
            "status": "already_running",
            "detail": "En indeksering kører allerede for denne organisation",
        }

    # Brand names help find files a supplier named after itself and the season
    # rather than using the word "order confirmation" at all.
    brands_result = await db.execute(
        select(Brand.name).where(
            Brand.organisation_id == org_id,
            Brand.is_active == True,  # noqa: E712 — SQLAlchemy needs the comparison
        )
    )
    vendor_names = [row[0] for row in brands_result.all() if row[0]]

    _indexing_orgs.add(org_id)
    background_tasks.add_task(_run_index, org_id, vendor_names, max_files)

    logger.info(
        "Queued Drive index for org=%s (%d brand name(s), max %d files)",
        str(org_id)[:8], len(vendor_names), max_files,
    )
    return {
        "status": "started",
        "brands_used": len(vendor_names),
        "max_files": max_files,
        "root_folder_id": connection.root_folder_id,
    }


@router.get("/drive/order-confirmations")
async def list_order_confirmations(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the order confirmations parsed for this organisation, newest first."""
    from app.models.order_confirmation import OrderConfirmation
    from app.services.order_confirmation_store import serialise_confirmation

    result = await db.execute(
        select(OrderConfirmation)
        .where(OrderConfirmation.organisation_id == user.organisation_id)
        .order_by(OrderConfirmation.parsed_at.desc())
        .limit(limit)
    )
    confirmations = result.scalars().all()

    return {
        "total": len(confirmations),
        # Line rows are omitted here — the list view only needs the headers,
        # and loading every line for every confirmation would be wasteful.
        "confirmations": [
            serialise_confirmation(c, include_lines=False) for c in confirmations
        ],
    }
