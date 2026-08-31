"""
Brand management — CRUD for brands + pre-populated suggestions.
"""
import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.brand import Brand

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Pre-populated brand suggestions ──────────────────────────────────

_SUGGESTIONS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "brand_suggestions.json"
_brand_suggestions: list[dict] | None = None


def _load_suggestions() -> list[dict]:
    global _brand_suggestions
    if _brand_suggestions is None:
        try:
            with open(_SUGGESTIONS_PATH, "r", encoding="utf-8") as f:
                _brand_suggestions = json.load(f)
        except FileNotFoundError:
            logger.warning("Brand suggestions file not found at %s", _SUGGESTIONS_PATH)
            _brand_suggestions = []
    return _brand_suggestions


# ── Pydantic schemas ─────────────────────────────────────────────────

class BrandResponse(BaseModel):
    id: str
    name: str
    slug: str
    markup: float = 2.5
    image_bank_url: str | None = None
    drive_folder_id: str | None = None
    image_bank_type: str | None = None
    image_bank_search_pattern: str | None = None
    image_bank_notes: str | None = None
    website_url: str | None = None
    search_url_pattern: str | None = None
    is_active: bool = True
    created_at: str | None = None

    model_config = {"from_attributes": True}


class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    markup: float = Field(2.5, ge=1.0, le=10.0, description="Markup multiplier (e.g. 2.5 = cost × 2.5)")
    image_bank_url: str | None = None
    drive_folder_id: str | None = None
    image_bank_type: str | None = Field(None, pattern=r"^(datadwell|canto|trendmark|brandos|custom)$")
    image_bank_search_pattern: str | None = None
    image_bank_notes: str | None = None
    website_url: str | None = None
    search_url_pattern: str | None = None


class BrandUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    markup: float | None = Field(None, ge=1.0, le=10.0)
    image_bank_url: str | None = None
    drive_folder_id: str | None = None
    image_bank_type: str | None = None
    image_bank_search_pattern: str | None = None
    image_bank_notes: str | None = None
    website_url: str | None = None
    search_url_pattern: str | None = None
    is_active: bool | None = None


class BrandSuggestion(BaseModel):
    name: str
    slug: str
    website: str | None = None
    search_url: str | None = None
    already_added: bool = False


# ── Helpers ──────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Convert brand name to URL-safe slug."""
    slug = name.lower().strip()
    # Replace special characters
    slug = slug.replace("°", "").replace("'", "").replace("'", "")
    slug = slug.replace("ø", "o").replace("å", "a").replace("æ", "ae")
    slug = slug.replace("ü", "u").replace("é", "e").replace("ê", "e")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/brands", response_model=list[BrandResponse])
async def list_brands(
    search: str = Query(None, description="Filter brands by name"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all brands for the current organisation."""
    query = select(Brand).where(
        Brand.organisation_id == user.organisation_id
    )
    if search:
        query = query.where(Brand.name.ilike(f"%{search}%"))

    query = query.order_by(Brand.name)
    result = await db.execute(query)
    brands = result.scalars().all()

    return [
        BrandResponse(
            id=str(b.id),
            name=b.name,
            slug=b.slug,
            markup=b.markup or 2.5,
            image_bank_url=b.image_bank_url,
            drive_folder_id=b.drive_folder_id,
            image_bank_type=b.image_bank_type,
            image_bank_search_pattern=b.image_bank_search_pattern,
            image_bank_notes=b.image_bank_notes,
            website_url=b.website_url,
            search_url_pattern=b.search_url_pattern,
            is_active=b.is_active,
            created_at=b.created_at.isoformat() if b.created_at else None,
        )
        for b in brands
    ]


@router.get("/brands/suggestions", response_model=list[BrandSuggestion])
async def brand_suggestions(
    search: str = Query("", description="Search for brand suggestions"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return pre-populated brand suggestions, filtered by search query.
    Marks brands that the org has already added.
    """
    suggestions = _load_suggestions()

    # Get existing brand slugs for this org
    result = await db.execute(
        select(Brand.slug).where(Brand.organisation_id == user.organisation_id)
    )
    existing_slugs = {row[0] for row in result.all()}

    # Filter by search
    search_lower = search.lower().strip()
    filtered = []
    for s in suggestions:
        if search_lower and search_lower not in s["name"].lower():
            continue
        filtered.append(
            BrandSuggestion(
                name=s["name"],
                slug=s["slug"],
                website=s.get("website"),
                search_url=s.get("search_url"),
                already_added=s["slug"] in existing_slugs,
            )
        )

    return filtered[:50]  # Cap at 50 results


@router.post("/brands", response_model=BrandResponse, status_code=201)
async def create_brand(
    data: BrandCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a brand to the organisation."""
    slug = data.slug or _slugify(data.name)

    # Check if brand already exists for this org
    existing = await db.execute(
        select(Brand).where(
            Brand.organisation_id == user.organisation_id,
            Brand.slug == slug,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Brand '{data.name}' er allerede tilføjet")

    brand = Brand(
        id=uuid.uuid4(),
        organisation_id=user.organisation_id,
        name=data.name,
        slug=slug,
        markup=data.markup,
        image_bank_url=data.image_bank_url,
        drive_folder_id=data.drive_folder_id,
        image_bank_type=data.image_bank_type,
        image_bank_search_pattern=data.image_bank_search_pattern,
        image_bank_notes=data.image_bank_notes,
        website_url=data.website_url,
        search_url_pattern=data.search_url_pattern,
    )
    db.add(brand)
    await db.flush()
    await db.refresh(brand)

    logger.info("Brand '%s' created for org %s (markup=%.2f)", brand.name, user.organisation_id, brand.markup)

    return BrandResponse(
        id=str(brand.id),
        name=brand.name,
        slug=brand.slug,
        markup=brand.markup or 2.5,
        image_bank_url=brand.image_bank_url,
        drive_folder_id=brand.drive_folder_id,
        image_bank_type=brand.image_bank_type,
        image_bank_search_pattern=brand.image_bank_search_pattern,
        image_bank_notes=brand.image_bank_notes,
        website_url=brand.website_url,
        search_url_pattern=brand.search_url_pattern,
        is_active=brand.is_active,
        created_at=brand.created_at.isoformat() if brand.created_at else None,
    )


@router.patch("/brands/{brand_id}", response_model=BrandResponse)
async def update_brand(
    brand_id: str,
    data: BrandUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a brand's details (name, image bank config, etc.)."""
    result = await db.execute(
        select(Brand).where(
            Brand.id == uuid.UUID(brand_id),
            Brand.organisation_id == user.organisation_id,
        )
    )
    brand = result.scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand ikke fundet")

    # Use model_fields_set to distinguish between "not provided" and
    # "explicitly set to null" — allows clearing optional fields.
    provided = data.model_fields_set

    if "name" in provided:
        brand.name = data.name
    if "markup" in provided:
        brand.markup = data.markup
    if "image_bank_url" in provided:
        brand.image_bank_url = data.image_bank_url
    if "drive_folder_id" in provided:
        brand.drive_folder_id = data.drive_folder_id
    if "image_bank_type" in provided:
        brand.image_bank_type = data.image_bank_type
    if "image_bank_search_pattern" in provided:
        brand.image_bank_search_pattern = data.image_bank_search_pattern
    if "image_bank_notes" in provided:
        brand.image_bank_notes = data.image_bank_notes
    if "website_url" in provided:
        brand.website_url = data.website_url
    if "search_url_pattern" in provided:
        brand.search_url_pattern = data.search_url_pattern
    if "is_active" in provided:
        brand.is_active = data.is_active

    await db.flush()
    await db.refresh(brand)

    logger.info("Brand '%s' updated (markup=%.2f)", brand.name, brand.markup)

    return BrandResponse(
        id=str(brand.id),
        name=brand.name,
        slug=brand.slug,
        markup=brand.markup or 2.5,
        image_bank_url=brand.image_bank_url,
        drive_folder_id=brand.drive_folder_id,
        image_bank_type=brand.image_bank_type,
        image_bank_search_pattern=brand.image_bank_search_pattern,
        image_bank_notes=brand.image_bank_notes,
        website_url=brand.website_url,
        search_url_pattern=brand.search_url_pattern,
        is_active=brand.is_active,
        created_at=brand.created_at.isoformat() if brand.created_at else None,
    )


@router.delete("/brands/{brand_id}", status_code=204)
async def delete_brand(
    brand_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a brand from the organisation."""
    result = await db.execute(
        select(Brand).where(
            Brand.id == uuid.UUID(brand_id),
            Brand.organisation_id == user.organisation_id,
        )
    )
    brand = result.scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand ikke fundet")

    await db.delete(brand)
    await db.flush()

    logger.info("Brand '%s' deleted from org %s", brand.name, user.organisation_id)


@router.get("/brands/stats")
async def brand_stats(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get brand statistics for the organisation."""
    result = await db.execute(
        select(func.count(Brand.id)).where(
            Brand.organisation_id == user.organisation_id
        )
    )
    total = result.scalar() or 0

    from sqlalchemy import or_
    result_with_bank = await db.execute(
        select(func.count(Brand.id)).where(
            Brand.organisation_id == user.organisation_id,
            or_(
                Brand.image_bank_url.isnot(None),
                Brand.image_bank_search_pattern.isnot(None),
            ),
        )
    )
    with_image_bank = result_with_bank.scalar() or 0

    return {
        "total_brands": total,
        "with_image_bank": with_image_bank,
        "without_image_bank": total - with_image_bank,
    }
