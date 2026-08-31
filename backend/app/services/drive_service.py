"""
Google Drive service — tokens, order-confirmation search, and file download.

get_valid_access_token() returns a usable access token, refreshing it via the
stored refresh token when it has expired.

search_order_confirmations() runs a series of ranked Drive queries — most
specific first — and merges the hits, keeping each file's best score.

Refresh goes through google-auth rather than a hand-rolled POST so that token
URI, clock skew and error handling come from the library. google-auth and the
Drive client are both synchronous, so every call runs in a thread to keep the
event loop free — the same approach imports.py uses for PDF parsing.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
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

# File types an order confirmation can arrive as.
MIME_PDF = "application/pdf"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_CSV = "text/csv"
MIME_GOOGLE_DOC = "application/vnd.google-apps.document"
SEARCHABLE_MIME_TYPES = (MIME_PDF, MIME_XLSX, MIME_CSV, MIME_GOOGLE_DOC)

# Google Workspace files have no bytes of their own and must be exported.
GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."

# Ranked match strengths, most specific first.
SCORE_ORDER_NUMBER = 100
SCORE_VENDOR_SEASON = 80
SCORE_SKU = 70
SCORE_VENDOR = 40

MAX_CANDIDATES = 5
# Per-query cap: enough to rank from, small enough to stay one page.
QUERY_PAGE_SIZE = 20

FILE_FIELDS = "files(id, name, mimeType, modifiedTime, webViewLink, size)"

# Indexing walks pages rather than taking the top few, so it asks for more per
# call and needs the page token back.
INDEX_PAGE_SIZE = 100
INDEX_FILE_FIELDS = f"nextPageToken, {FILE_FIELDS}"

# Words that mark a file as an order confirmation, across the languages our
# suppliers write in. Matched against the filename only — Drive's "name
# contains" is cheap, whereas fullText search would drag in every invoice.
ORDER_CONFIRMATION_NAME_HINTS = (
    "order confirmation",
    "orderconfirmation",
    "order-confirmation",
    "order_confirmation",
    "ordrebekræftelse",
    "ordrebekraeftelse",
    "orderbekräftelse",
    "auftragsbestätigung",
    "auftragsbestaetigung",
    "confirmation de commande",
    "conferma d'ordine",
    "sales order",
    "purchase order",
    "orderconf",
    "order conf",
)


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


# ═══════════════════════════════════════════════
# Season variants
# ═══════════════════════════════════════════════

# Codes that mean the same season. A brand writing "AV26" may file the same
# order under "AW26" or "FW26", so all of them are worth searching for.
_SEASON_CODE_FAMILIES = {
    "AW": ("AW", "AV", "FW", "AH", "HW"),
    "SS": ("SS", "PE"),
    "PS": ("PS",),
    "PF": ("PF",),
}

# Spelled-out forms, searched without a year — a filename rarely writes
# "Spring Summer 2027", and these are always ANDed with the vendor name.
_SEASON_WORD_FORMS = {
    "AW": ("Autumn Winter", "Fall Winter", "A/W", "F/W"),
    "SS": ("Spring Summer", "S/S"),
    "PS": ("Pre-Spring", "Pre Spring"),
    "PF": ("Pre-Fall", "Pre Fall", "Resort"),
}

# Checked in order — "PRESPRING" must not fall through to the "SPRING" rule.
_SEASON_FAMILY_WORDS = (
    ("PRESPRING", "PS"),
    ("PREFALL", "PF"),
    ("PREAUTUMN", "PF"),
    ("RESORT", "PF"),
    ("CRUISE", "PF"),
    ("FALLWINTER", "AW"),
    ("AUTUMNWINTER", "AW"),
    ("HERBSTWINTER", "AW"),
    ("SPRINGSUMMER", "SS"),
    ("AUTUMN", "AW"),
    ("WINTER", "AW"),
    ("FALL", "AW"),
    ("SPRING", "SS"),
    ("SUMMER", "SS"),
)

_SEASON_EXACT_FAMILY = {
    "AW": "AW", "AV": "AW", "FW": "AW", "AH": "AW", "HW": "AW",
    "SS": "SS", "PE": "SS",
    "PS": "PS", "PF": "PF",
    "E": "SS", "H": "AW",
}

_YEAR_RE = re.compile(r"(?:19|20)(\d{2})(?!\d)|(?<!\d)(\d{2})(?!\d)")


def season_search_variants(season: str) -> list[str]:
    """
    Expand a season into the strings worth searching Drive filenames for.

        "AV26" -> ["AV26", "AW26", "FW26", "AH26", "HW26",
                   "Autumn Winter", "Fall Winter", "A/W", "F/W"]
        "SS27" -> ["SS27", "PE27", "Spring Summer", "S/S"]

    The season as given always comes first. An unrecognised season yields just
    itself, so a brand-specific code is still searched rather than dropped.
    """
    if not season or not season.strip():
        return []

    variants: list[str] = [season.strip()]

    compact = re.sub(r"[^A-Za-z0-9]", "", season).upper()
    letters = re.sub(r"[^A-Z]", "", compact)
    year_match = _YEAR_RE.search(compact)
    year = (year_match.group(1) or year_match.group(2)) if year_match else ""

    family = _SEASON_EXACT_FAMILY.get(letters)
    if family is None:
        for word, mapped in _SEASON_FAMILY_WORDS:
            if word in letters:
                family = mapped
                break

    if family is None:
        return variants

    # Bare codes are only useful with a year — "SS" on its own matches far too
    # much — but the spelled-out forms are specific enough to search alone.
    if year:
        variants.extend(f"{code}{year}" for code in _SEASON_CODE_FAMILIES[family])
    variants.extend(_SEASON_WORD_FORMS[family])

    seen: set[str] = set()
    deduped: list[str] = []
    for variant in variants:
        key = variant.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(variant)
    return deduped


# ═══════════════════════════════════════════════
# Order confirmation search
# ═══════════════════════════════════════════════

@dataclass
class DriveCandidate:
    """One Drive file that might be the order confirmation for an invoice."""

    file_id: str
    name: str
    mime_type: str
    modified_time: str | None
    score: int
    matched_by: str
    web_view_link: str | None = None

    def as_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "modified_time": self.modified_time,
            "score": self.score,
            "matched_by": self.matched_by,
            "web_view_link": self.web_view_link,
        }


def _escape_query_value(value: str) -> str:
    """
    Escape a value for a Drive query string literal.

    Without this a vendor like "Levi's" would close the quote early and Drive
    would reject the whole query.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _mime_clause() -> str:
    return "(" + " or ".join(f"mimeType = '{m}'" for m in SEARCHABLE_MIME_TYPES) + ")"


def _wrap_query(clause: str, root_folder_id: str | None) -> str:
    """Add the constraints every search shares: not trashed, right file type,
    and — when the organisation set one — inside the configured root folder."""
    parts = [f"({clause})", "trashed = false", _mime_clause()]
    if root_folder_id:
        parts.append(f"'{_escape_query_value(root_folder_id)}' in parents")
    return " and ".join(parts)


def build_ranked_queries(
    vendor_name: str,
    order_number: str | None = None,
    season: str | None = None,
    skus: list[str] | None = None,
    root_folder_id: str | None = None,
    max_skus: int = 10,
) -> list[tuple[int, str, str]]:
    """
    Build the Drive queries to try, as (score, matched_by, query) in descending
    score order — most specific match first.
    """
    queries: list[tuple[int, str, str]] = []
    vendor = (vendor_name or "").strip()

    if order_number and order_number.strip():
        value = _escape_query_value(order_number.strip())
        queries.append((
            SCORE_ORDER_NUMBER,
            "order_number",
            _wrap_query(f"fullText contains '{value}'", root_folder_id),
        ))

    if vendor and season:
        variants = season_search_variants(season)
        if variants:
            season_clause = " or ".join(
                f"name contains '{_escape_query_value(v)}'" for v in variants
            )
            queries.append((
                SCORE_VENDOR_SEASON,
                "vendor+season",
                _wrap_query(
                    f"name contains '{_escape_query_value(vendor)}' and ({season_clause})",
                    root_folder_id,
                ),
            ))

    # One query per SKU, capped so a 200-line invoice cannot fire 200 API calls.
    for sku in (skus or [])[:max_skus]:
        if not sku or not sku.strip():
            continue
        value = _escape_query_value(sku.strip())
        queries.append((
            SCORE_SKU,
            f"sku:{sku.strip()}",
            _wrap_query(f"fullText contains '{value}'", root_folder_id),
        ))

    if vendor:
        queries.append((
            SCORE_VENDOR,
            "vendor",
            _wrap_query(
                f"name contains '{_escape_query_value(vendor)}'", root_folder_id
            ),
        ))

    return queries


def _run_ranked_queries(client, queries: list[tuple[int, str, str]]) -> list[DriveCandidate]:
    """
    Execute the ranked queries and merge the hits, keeping each file's best
    score. Blocking — call through asyncio.to_thread.
    """
    best: dict[str, DriveCandidate] = {}

    for score, matched_by, query in queries:
        # Scores only ever descend, so once there are enough candidates no
        # later query can change the top of the list.
        if len(best) >= MAX_CANDIDATES:
            break

        try:
            response = client.files().list(
                q=query,
                pageSize=QUERY_PAGE_SIZE,
                fields=FILE_FIELDS,
                orderBy="modifiedTime desc",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        except Exception as e:
            # One failing query must not sink the whole search — a broader
            # query may still find the file.
            logger.warning("Drive query failed (%s): %s", matched_by, e)
            continue

        for item in response.get("files", []) or []:
            file_id = item.get("id")
            if not file_id:
                continue
            existing = best.get(file_id)
            if existing is not None and existing.score >= score:
                continue
            best[file_id] = DriveCandidate(
                file_id=file_id,
                name=item.get("name", ""),
                mime_type=item.get("mimeType", ""),
                modified_time=item.get("modifiedTime"),
                score=score,
                matched_by=matched_by,
                web_view_link=item.get("webViewLink"),
            )

    ranked = sorted(
        best.values(),
        key=lambda c: (c.score, c.modified_time or ""),
        reverse=True,
    )
    return ranked[:MAX_CANDIDATES]


async def search_order_confirmations(
    db: AsyncSession,
    org_id: uuid.UUID,
    vendor_name: str,
    order_number: str | None = None,
    season: str | None = None,
    skus: list[str] | None = None,
) -> list[DriveCandidate]:
    """
    Find the Drive files most likely to be the order confirmation for an invoice.

    Tries the most specific signal first — the exact order number in the file
    text — then vendor plus season in the filename, then each SKU in the file
    text, and finally the vendor name alone. Returns at most five candidates,
    best score first, newest first within a score.

    Returns an empty list when the organisation has no usable Drive connection.

    Note: when a root folder is configured the search is limited to its direct
    children. Drive's "in parents" is not recursive, so files in subfolders of
    the root will not be found.
    """
    access_token = await get_valid_access_token(db, org_id)
    if not access_token:
        logger.info("Drive search skipped for org %s — not connected", org_id)
        return []

    connection = await get_drive_connection(db, org_id)
    root_folder_id = connection.root_folder_id if connection else None

    queries = build_ranked_queries(
        vendor_name=vendor_name,
        order_number=order_number,
        season=season,
        skus=skus,
        root_folder_id=root_folder_id,
    )
    if not queries:
        return []

    client = build_drive_client(access_token)
    candidates = await asyncio.to_thread(_run_ranked_queries, client, queries)

    logger.info(
        "Drive search for org=%s vendor=%r found %d candidate(s)",
        org_id, vendor_name, len(candidates),
    )
    return candidates


# ═══════════════════════════════════════════════
# Download
# ═══════════════════════════════════════════════

def _download_sync(client, file_id: str) -> bytes:
    """Blocking download. Google Workspace files are exported as PDF."""
    metadata = client.files().get(
        fileId=file_id,
        fields="id, name, mimeType",
        supportsAllDrives=True,
    ).execute()

    mime_type = metadata.get("mimeType", "")
    if mime_type.startswith(GOOGLE_NATIVE_PREFIX):
        # A Google Doc has no bytes of its own — ask Drive to render one.
        return client.files().export_media(fileId=file_id, mimeType=MIME_PDF).execute()

    return client.files().get_media(fileId=file_id).execute()


# ═══════════════════════════════════════════════
# Indexing — find every order confirmation up front
# ═══════════════════════════════════════════════

def looks_like_order_confirmation(name: str, vendor_names: list[str] | None = None) -> bool:
    """
    Whether a filename looks like an order confirmation.

    True for a confirmation keyword in any of our suppliers' languages, or for a
    known vendor name — brands often name the file after themselves and the
    season alone. Used to filter what Drive returns, so a broad query cannot
    drag unrelated files into the parse queue.
    """
    if not name:
        return False
    lowered = name.casefold()

    if any(hint in lowered for hint in ORDER_CONFIRMATION_NAME_HINTS):
        return True

    for vendor in vendor_names or []:
        vendor = (vendor or "").strip().casefold()
        if len(vendor) >= 3 and vendor in lowered:
            return True

    return False


def build_index_queries(
    vendor_names: list[str] | None = None,
    root_folder_id: str | None = None,
    *,
    terms_per_query: int = 20,
    max_vendors: int = 40,
) -> list[str]:
    """
    Build the Drive queries that sweep for order confirmations.

    Terms are batched rather than sent one per call: Drive accepts a long OR
    chain, and one request per brand would be dozens of round trips.
    """
    terms: list[str] = list(ORDER_CONFIRMATION_NAME_HINTS)
    for vendor in (vendor_names or [])[:max_vendors]:
        vendor = (vendor or "").strip()
        if len(vendor) >= 3:
            terms.append(vendor)

    queries: list[str] = []
    for start in range(0, len(terms), terms_per_query):
        batch = terms[start:start + terms_per_query]
        clause = " or ".join(
            f"name contains '{_escape_query_value(term)}'" for term in batch
        )
        queries.append(_wrap_query(clause, root_folder_id))
    return queries


def _run_index_queries(client, queries: list[str], max_files: int) -> list[dict]:
    """
    Execute the sweep, following pagination. Blocking — call via to_thread.

    Returns raw Drive file dicts, deduplicated by id.
    """
    seen: dict[str, dict] = {}

    for query in queries:
        page_token = None
        while True:
            if len(seen) >= max_files:
                return list(seen.values())
            try:
                response = client.files().list(
                    q=query,
                    pageSize=INDEX_PAGE_SIZE,
                    fields=INDEX_FILE_FIELDS,
                    orderBy="modifiedTime desc",
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
            except Exception as e:
                logger.warning("Drive index query failed: %s", e)
                break

            for item in response.get("files", []) or []:
                file_id = item.get("id")
                if file_id and file_id not in seen:
                    seen[file_id] = item

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    return list(seen.values())


async def list_order_confirmation_candidates(
    db: AsyncSession,
    org_id: uuid.UUID,
    vendor_names: list[str] | None = None,
    max_files: int = 200,
) -> list[DriveCandidate]:
    """
    Sweep Drive for everything that looks like an order confirmation.

    Restricted to the organisation's root folder when one is set. Filenames are
    re-checked locally after the query, because Drive's "name contains" is a
    loose token match and will return more than the terms strictly imply.

    Returns an empty list when the organisation has no usable Drive connection.
    """
    access_token = await get_valid_access_token(db, org_id)
    if not access_token:
        logger.info("Drive index skipped for org %s — not connected", org_id)
        return []

    connection = await get_drive_connection(db, org_id)
    root_folder_id = connection.root_folder_id if connection else None

    queries = build_index_queries(vendor_names, root_folder_id)
    client = build_drive_client(access_token)
    raw_files = await asyncio.to_thread(
        _run_index_queries, client, queries, max_files
    )

    candidates = [
        DriveCandidate(
            file_id=item["id"],
            name=item.get("name", ""),
            mime_type=item.get("mimeType", ""),
            modified_time=item.get("modifiedTime"),
            score=SCORE_VENDOR,
            matched_by="index",
            web_view_link=item.get("webViewLink"),
        )
        for item in raw_files
        if looks_like_order_confirmation(item.get("name", ""), vendor_names)
    ]

    logger.info(
        "Drive index for org=%s: %d file(s) returned, %d look like confirmations",
        org_id, len(raw_files), len(candidates),
    )
    return candidates


def _get_file_metadata_sync(client, file_id: str) -> dict:
    return client.files().get(
        fileId=file_id,
        fields="id, name, mimeType, modifiedTime, size",
        supportsAllDrives=True,
    ).execute()


async def get_file_metadata(
    db: AsyncSession, org_id: uuid.UUID, file_id: str
) -> dict | None:
    """
    Fetch a Drive file's name, MIME type and modifiedTime.

    The modifiedTime is what the order-confirmation cache keys on, so this is
    called before deciding whether a re-parse is needed.
    """
    access_token = await get_valid_access_token(db, org_id)
    if not access_token:
        return None
    client = build_drive_client(access_token)
    return await asyncio.to_thread(_get_file_metadata_sync, client, file_id)


async def download_file(
    db: AsyncSession, org_id: uuid.UUID, file_id: str
) -> bytes | None:
    """
    Download a Drive file's bytes.

    Native files (PDF, XLSX, CSV) come back as stored; Google Docs are exported
    to PDF so the rest of the pipeline can treat every source the same way.

    Returns None when the organisation has no usable Drive connection. Errors
    from the Drive API itself propagate — a failed download of a file the user
    picked is not something to swallow.
    """
    access_token = await get_valid_access_token(db, org_id)
    if not access_token:
        logger.info("Drive download skipped for org %s — not connected", org_id)
        return None

    client = build_drive_client(access_token)
    return await asyncio.to_thread(_download_sync, client, file_id)
