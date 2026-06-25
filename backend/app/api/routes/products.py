"""
Product endpoints — update products before pushing to Shopify.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.models.import_product import ImportProduct
from app.models.import_record import Import

router = APIRouter()


class VariantUpdate(BaseModel):
    """Schema for a single product variant."""
    size: str = ""
    quantity: int = Field(default=0, ge=0)
    ean: str | None = None


class ProductUpdate(BaseModel):
    """Fields the user can edit before pushing."""
    title: str | None = None
    vendor: str | None = None
    product_type: str | None = None
    description_da: str | None = None
    description_en: str | None = None
    color: str | None = None
    color_original: str | None = None
    retail_price_dkk: float | None = None
    variants: list[VariantUpdate] | None = None
    images: list[str] | None = None
    status: str | None = None  # approved / skipped
    material: str | None = None
    gender: str | None = None
    season: str | None = None
    country_of_origin: str | None = None
    hs_code: str | None = None
    ai_tags: list[str] | None = None
    handle: str | None = None


@router.patch("/{product_id}")
async def update_product(
    product_id: str,
    body: ProductUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a product before push (edit title, price, variants, etc.)."""
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID format")

    result = await db.execute(
        select(ImportProduct)
        .join(Import, ImportProduct.import_id == Import.id)
        .where(
            ImportProduct.id == product_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if product.status == "pushed":
        raise HTTPException(status_code=400, detail="Cannot edit a pushed product")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(product, key, value)

    if update_data:
        product.is_edited = True

    await db.flush()

    return {
        "id": str(product.id),
        "title": product.title,
        "status": product.status,
        "is_edited": product.is_edited,
    }


@router.post("/{product_id}/approve")
async def approve_product(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a product as approved for push."""
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID format")

    result = await db.execute(
        select(ImportProduct)
        .join(Import, ImportProduct.import_id == Import.id)
        .where(
            ImportProduct.id == product_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.status = "approved"
    await db.flush()

    return {"id": str(product.id), "status": "approved"}


@router.post("/{product_id}/skip")
async def skip_product(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Skip a product — won't be pushed to Shopify."""
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID format")

    result = await db.execute(
        select(ImportProduct)
        .join(Import, ImportProduct.import_id == Import.id)
        .where(
            ImportProduct.id == product_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.status = "skipped"
    await db.flush()

    return {"id": str(product.id), "status": "skipped"}
