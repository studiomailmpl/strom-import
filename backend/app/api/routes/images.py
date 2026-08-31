"""
Product image endpoints — upload, serve, delete, reorder.
Uploaded images take priority over web-scraped images during Shopify push.
"""
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.import_product import ImportProduct
from app.models.import_record import Import
from app.models.product_image import ProductImage
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif"}
MAX_IMAGES_PER_PRODUCT = 10

# Magic byte signatures for image validation
_MAGIC_BYTES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG"],
    "image/webp": [b"RIFF"],  # WebP starts with RIFF....WEBP
    "image/avif": [b"\x00\x00\x00"],  # AVIF is an ISOBMFF container (ftyp box)
}


def _validate_magic_bytes(content: bytes, content_type: str) -> bool:
    """Validate that file content matches the claimed content type via magic bytes."""
    signatures = _MAGIC_BYTES.get(content_type)
    if not signatures:
        return False
    for sig in signatures:
        if content[:len(sig)] == sig:
            # Extra check for WebP: bytes 8-12 must be "WEBP"
            if content_type == "image/webp" and content[8:12] != b"WEBP":
                return False
            return True
    return False


# ── Helpers ──────────────────────────────────────────────────────────

def _get_image_dir() -> Path:
    """Get/create the image upload directory with restricted permissions."""
    path = Path(settings.image_upload_dir)
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return path


async def _get_product_with_access(
    product_id: str,
    user: User,
    db: AsyncSession,
) -> ImportProduct:
    """Fetch product and verify the user's org owns it."""
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldigt produkt-ID")

    result = await db.execute(
        select(ImportProduct)
        .join(Import, ImportProduct.import_id == Import.id)
        .where(
            ImportProduct.id == pid,
            Import.organisation_id == user.organisation_id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Produkt ikke fundet")
    return product


# ── Pydantic schemas ─────────────────────────────────────────────────

class ImageResponse(BaseModel):
    id: str
    product_id: str
    filename: str
    content_type: str
    file_size: int
    sort_order: int
    url: str  # Public serve URL
    source: str  # "uploaded"


class ReorderRequest(BaseModel):
    image_ids: list[str]  # Ordered list of image IDs


# ── Upload images ────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/images",
    response_model=list[ImageResponse],
    status_code=201,
)
async def upload_product_images(
    product_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload one or more images for a product.
    Supports JPEG, PNG, WebP, AVIF. Max 10 images per product.
    """
    product = await _get_product_with_access(product_id, user, db)

    if product.status == "pushed":
        raise HTTPException(status_code=400, detail="Kan ikke tilføje billeder til et pushet produkt")

    # Check current count
    count_result = await db.execute(
        select(func.count(ProductImage.id)).where(
            ProductImage.product_id == product.id
        )
    )
    current_count = count_result.scalar() or 0

    if current_count + len(files) > MAX_IMAGES_PER_PRODUCT:
        raise HTTPException(
            status_code=400,
            detail=f"Maks {MAX_IMAGES_PER_PRODUCT} billeder per produkt. Du har {current_count}, forsøger at tilføje {len(files)}.",
        )

    # Validate ALL files before saving any (type + size)
    max_bytes = settings.max_image_size_mb * 1024 * 1024
    file_contents: list[tuple[UploadFile, bytes]] = []

    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Filtype '{f.content_type}' er ikke tilladt. Brug JPEG, PNG, WebP eller AVIF.",
            )
        content = await f.read()
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"'{f.filename}' er for stor ({len(content) / 1024 / 1024:.1f} MB). Maks {settings.max_image_size_mb} MB.",
            )
        if len(content) == 0:
            raise HTTPException(
                status_code=400,
                detail=f"'{f.filename}' er tom.",
            )
        # Validate magic bytes to prevent spoofed content types (e.g. SVG with JS)
        if not _validate_magic_bytes(content, f.content_type):
            raise HTTPException(
                status_code=400,
                detail=f"'{f.filename}' indeholder ikke gyldige billeddata for typen '{f.content_type}'.",
            )
        file_contents.append((f, content))

    # All validated — now write to disk
    image_dir = _get_image_dir()
    product_dir = image_dir / str(product.id)
    product_dir.mkdir(parents=True, exist_ok=True, mode=0o750)

    created_images: list[ProductImage] = []
    written_files: list[Path] = []  # Track for cleanup on error

    try:
        for idx, (f, content) in enumerate(file_contents):
            # Generate unique filename
            ext = Path(f.filename or "image.jpg").suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".avif"}:
                ext = ".jpg"
            unique_name = f"{uuid.uuid4().hex[:12]}{ext}"
            file_path = product_dir / unique_name

            # Write to disk
            with open(file_path, "wb") as out:
                out.write(content)
            written_files.append(file_path)

            # Create DB record
            relative_path = f"{product.id}/{unique_name}"
            image = ProductImage(
                id=uuid.uuid4(),
                product_id=product.id,
                filename=f.filename or unique_name,
                file_path=relative_path,
                content_type=f.content_type or "image/jpeg",
                file_size=len(content),
                sort_order=current_count + idx,
            )
            db.add(image)
            created_images.append(image)

        await db.flush()

        # Race condition guard: re-check total count after insert.
        # If a concurrent request snuck in, we may have exceeded the limit.
        recheck_result = await db.execute(
            select(func.count(ProductImage.id)).where(
                ProductImage.product_id == product.id
            )
        )
        final_count = recheck_result.scalar() or 0
        if final_count > MAX_IMAGES_PER_PRODUCT:
            # Roll back our inserts by expunging them and cleaning up files
            for img in created_images:
                await db.delete(img)
            await db.flush()
            for fp in written_files:
                try:
                    fp.unlink(missing_ok=True)
                except OSError:
                    pass
            raise HTTPException(
                status_code=409,
                detail=f"Maks {MAX_IMAGES_PER_PRODUCT} billeder per produkt. En anden upload nåede grænsen først.",
            )

    except HTTPException:
        raise
    except Exception:
        # Clean up any files written before the error
        for fp in written_files:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    logger.info(
        "Uploaded %d images for product '%s' (id=%s)",
        len(created_images),
        product.title,
        product.id,
    )

    base_url = settings.public_base_url.rstrip("/")
    return [
        ImageResponse(
            id=str(img.id),
            product_id=str(img.product_id),
            filename=img.filename,
            content_type=img.content_type,
            file_size=img.file_size,
            sort_order=img.sort_order,
            url=f"{base_url}/api/v1/images/{img.file_path}",
            source="uploaded",
        )
        for img in created_images
    ]


# ── List product images ──────────────────────────────────────────────

@router.get("/products/{product_id}/images", response_model=list[ImageResponse])
async def list_product_images(
    product_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all uploaded images for a product."""
    product = await _get_product_with_access(product_id, user, db)

    result = await db.execute(
        select(ProductImage)
        .where(ProductImage.product_id == product.id)
        .order_by(ProductImage.sort_order)
    )
    images = result.scalars().all()

    base_url = settings.public_base_url.rstrip("/")
    return [
        ImageResponse(
            id=str(img.id),
            product_id=str(img.product_id),
            filename=img.filename,
            content_type=img.content_type,
            file_size=img.file_size,
            sort_order=img.sort_order,
            url=f"{base_url}/api/v1/images/{img.file_path}",
            source="uploaded",
        )
        for img in images
    ]


# ── Delete image ─────────────────────────────────────────────────────

@router.delete("/products/{product_id}/images/{image_id}", status_code=204)
async def delete_product_image(
    product_id: str,
    image_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an uploaded image."""
    product = await _get_product_with_access(product_id, user, db)

    try:
        img_uuid = uuid.UUID(image_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldigt billed-ID")

    result = await db.execute(
        select(ProductImage).where(
            ProductImage.id == img_uuid,
            ProductImage.product_id == product.id,
        )
    )
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Billede ikke fundet")

    # Delete file from disk
    image_dir = _get_image_dir()
    file_path = image_dir / image.file_path
    if file_path.exists():
        file_path.unlink()
        logger.info("Deleted image file: %s", file_path)

    await db.delete(image)
    await db.flush()


# ── Reorder images ───────────────────────────────────────────────────

@router.patch("/products/{product_id}/images/reorder")
async def reorder_product_images(
    product_id: str,
    body: ReorderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder images by providing the image IDs in desired order."""
    product = await _get_product_with_access(product_id, user, db)

    result = await db.execute(
        select(ProductImage).where(ProductImage.product_id == product.id)
    )
    images = {str(img.id): img for img in result.scalars().all()}

    for order, img_id in enumerate(body.image_ids):
        if img_id in images:
            images[img_id].sort_order = order

    await db.flush()

    return {"status": "ok", "count": len(body.image_ids)}


# ── Serve images (public, no auth — rate-limited) ──────────────────

@router.get("/images/{product_id}/{filename}")
@limiter.limit("120/minute")
async def serve_image(request: Request, product_id: str, filename: str):
    """
    Serve an uploaded image file. This endpoint is PUBLIC (no auth)
    because Shopify needs to download images from it.
    """
    # Security: validate product_id is a valid UUID (prevents path traversal via "../../")
    try:
        uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID format")

    # Security: reject filenames with path separators or traversal patterns
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Security: only allow known image extensions
    ext = Path(filename).suffix.lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    image_dir = _get_image_dir()
    file_path = image_dir / product_id / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    # Security: ensure resolved path doesn't escape image directory (blocks symlinks)
    resolved = file_path.resolve(strict=True)
    try:
        resolved.relative_to(image_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".avif": "image/avif",
    }
    content_type = content_types.get(ext, "image/jpeg")

    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/images/drive/{vendor}/{sku}/{filename}")
@limiter.limit("120/minute")
async def serve_brand_drive_image(
    request: Request, vendor: str, sku: str, filename: str
):
    """
    Serve a packshot pulled from a brand's Google Drive folder.

    A separate route from serve_image because those images belong to a brand and
    a SKU, not to a product row — they are fetched during enrichment, before any
    product has an id. Public for the same reason: Shopify downloads images by
    URL, and Drive itself is behind OAuth.

    Path segments are re-validated here even though image_service sanitises them
    on the way in; this route is reachable directly.
    """
    for segment in (vendor, sku, filename):
        if "/" in segment or "\\" in segment or ".." in segment or segment.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid path")

    ext = Path(filename).suffix.lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    image_dir = _get_image_dir()
    file_path = image_dir / "drive" / vendor / sku / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    # Ensure the resolved path stays inside the image directory (blocks symlinks)
    resolved = file_path.resolve(strict=True)
    try:
        resolved.relative_to(image_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".avif": "image/avif",
    }

    return FileResponse(
        path=str(file_path),
        media_type=content_types.get(ext, "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )
