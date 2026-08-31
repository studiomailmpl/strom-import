"""
Product endpoints — update products before pushing to Shopify.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.models.import_product import ImportProduct
from app.models.import_record import Import
from app.models.product_image import ProductImage

router = APIRouter()


async def _serialize_product(product: ImportProduct, db: AsyncSession) -> dict:
    """
    Serialise a product in the same shape as GET /imports/{id} returns its
    products, so the frontend can merge a PATCH response straight into state.
    """
    base_url = get_settings().public_base_url.rstrip("/")

    images_result = await db.execute(
        select(ProductImage)
        .where(ProductImage.product_id == product.id)
        .order_by(ProductImage.sort_order)
    )
    uploaded_images = [
        {
            "id": str(img.id),
            "filename": img.filename,
            "url": f"{base_url}/api/v1/images/{img.file_path}",
            "source": "uploaded",
            "sort_order": img.sort_order,
        }
        for img in images_result.scalars().all()
    ]

    return {
        "id": str(product.id),
        "title": product.title,
        "vendor": product.vendor,
        "product_type": product.product_type,
        "description_da": product.description_da,
        "description_en": product.description_en or "",
        "style_code": product.style_code,
        "color": product.color,
        "cost_price_eur": product.cost_price_eur,
        "gross_price_eur": product.gross_price_eur,
        "discount_pct": product.discount_pct or 0,
        "retail_price_dkk": product.retail_price_dkk,
        "variants": product.variants,
        "images": product.images,
        "uploaded_images": uploaded_images,
        "status": product.status,
        "shopify_product_id": product.shopify_product_id,
        "is_restock": product.is_restock,
        "shopify_match_id": product.shopify_match_id,
        "shopify_match_title": product.shopify_match_title,
        "duplicate_of_import_id": (
            str(product.duplicate_of_import_id)
            if product.duplicate_of_import_id
            else None
        ),
        "duplicate_import_date": product.duplicate_import_date,
        "seo_keywords": product.seo_keywords or [],
        "qa_warnings": product.qa_warnings or [],
        "is_edited": product.is_edited,
    }


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

    return await _serialize_product(product, db)


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
