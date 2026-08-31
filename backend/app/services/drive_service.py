"""
Google Drive service — token handling for the per-organisation Drive connection.

get_valid_access_token() returns a usable access token, refreshing it via the
stored refresh token when it has expired.

Refresh goes through google-auth rather than a hand-rolled POST so that token
URI, clock skew and error handling come from the library. google-auth is
synchronous, so the refresh runs in a thread to keep the event loop free —
the same approach imports.py uses for PDF parsing.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token, encrypt_token
from app.models.drive_connection import DriveConnection

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Refresh a little before the token actually expires, so a token cannot lapse
# between this check and the API call that uses it.
EXPIRY_SKEW = timedelta(seconds=60)


async def get_drive_connection(
    db: AsyncSession, org_id: uuid.UUID
) -> DriveConnection | None:
    """Return the organisation's Drive connection, or None if not connected."""
    result = await db.execute(
        select(DriveConnection).where(DriveConnection.organisation_id == org_id)
    )
    return result.scalar_one_or_none()


def _refresh_sync(refresh_token: str) -> tuple[str, datetime | None]:
    """Blocking token refresh. Runs in a worker thread. Raises on failure."""
    settings = get_settings()
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=[DRIVE_SCOPE],
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials.token, credentials.expiry


async def get_valid_access_token(
    db: AsyncSession, org_id: uuid.UUID
) -> str | None:
    """
    Return a valid Drive access token for the organisation.

    Refreshes automatically when the stored token has expired. Returns None if
    the organisation has no Drive connection, has no refresh token, or the
    refresh was rejected — callers should treat that as "not connected".

    Takes the session as its first argument, matching the other services in
    this package; a token refresh writes back to the same transaction.
    """
    connection = await get_drive_connection(db, org_id)
    if not connection or not connection.encrypted_access_token:
        return None

    # Still valid? Hand back what we have.
    expires_at = connection.token_expires_at
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at - EXPIRY_SKEW > datetime.now(timezone.utc):
            return decrypt_token(connection.encrypted_access_token)

    if not connection.encrypted_refresh_token:
        logger.error("No Drive refresh token stored for org %s", org_id)
        return None

    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
        new_token, expiry = await asyncio.to_thread(_refresh_sync, refresh_token)
    except Exception as e:
        logger.error("Drive token refresh failed for org %s: %s", org_id, e)
        return None

    if not new_token:
        logger.error("Drive token refresh returned no token for org %s", org_id)
        return None

    connection.encrypted_access_token = encrypt_token(new_token)
    # google-auth reports expiry as a naive UTC datetime.
    if expiry:
        connection.token_expires_at = (
            expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
        )
    else:
        connection.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=3600)
    await db.commit()

    logger.info("Refreshed Drive access token for org %s", org_id)
    return new_token


def build_drive_client(access_token: str):
    """
    Build a Google Drive v3 client from an access token.

    Synchronous — the returned client blocks on every call, so wrap usage in
    asyncio.to_thread from async code.
    """
    credentials = Credentials(token=access_token, scopes=[DRIVE_SCOPE])
    return build("drive", "v3", credentials=credentials, cache_discovery=False)
