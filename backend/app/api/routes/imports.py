"""
Import endpoints — upload PDFs, trigger analysis, stream progress, get status.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db, async_session
from app.models.user import User
from app.models.import_record import Import
from app.models.import_file import ImportFile
from app.models.import_product import ImportProduct
from app.services.pdf_service import extract_pdf_text, extract_pdf_pages_as_images
from app.services.invoice_parser import parse_invoice_metadata, parse_invoice_tables
from app.models.brand import Brand
from app.models.product_image import ProductImage
from app.models.order_confirmation import OrderConfirmationLine
from app.services.ai_extractor import extract_products_with_ai, normalize_season
from app.services.image_service import (
    find_product_images_and_details,
    get_cached_images,
    save_image_cache,
    verify_and_filter_images,
)
from app.services.product_enrichment import (
    calculate_retail_price,
    map_type_danish,
    sort_sizes,
    build_description_da,
)

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory SSE progress store with automatic cleanup
# ---------------------------------------------------------------------------
_analysis_progress: dict[str, list[dict]] = {}
_analysis_progress_timestamps: dict[str, float] = {}

_PROGRESS_TTL_SECONDS = 600  # 10 minutes


def _cleanup_stale_progress() -> None:
    """Remove progress entries older than 10 minutes."""
    now = time.time()
    stale_keys = [
        key
        for key, ts in _analysis_progress_timestamps.items()
        if now - ts > _PROGRESS_TTL_SECONDS
    ]
    for key in stale_keys:
        _analysis_progress.pop(key, None)
        _analysis_progress_timestamps.pop(key, None)


_MAX_EVENTS_PER_IMPORT = 200  # Prevent unbounded memory growth


def _emit_event(import_id: str, event: dict) -> None:
    """Append an SSE event to the progress store for the given import."""
    _cleanup_stale_progress()
    if import_id not in _analysis_progress:
        _analysis_progress[import_id] = []
        _analysis_progress_timestamps[import_id] = time.time()
    events = _analysis_progress[import_id]
    events.append(event)
    # Prune oldest events if we exceed the cap (keep terminal events like done/error)
    if len(events) > _MAX_EVENTS_PER_IMPORT:
        _analysis_progress[import_id] = events[-_MAX_EVENTS_PER_IMPORT:]


async def _load_order_confirmation_lines(db, org_id, candidate):
    """
    Get the parsed lines for a Drive candidate, reusing the stored parse when
    Drive reports the file unchanged. Returns (confirmation, lines).
    """
    from app.services.drive_service import download_file
    from app.services.order_confirmation_parser import parse_order_confirmation
    from app.services.order_confirmation_store import (
        get_cached_confirmation,
        save_confirmation,
    )

    cached = await get_cached_confirmation(
        db, org_id, candidate.file_id, candidate.modified_time
    )
    if cached is not None:
        return cached, list(cached.lines)

    file_bytes = await download_file(db, org_id, candidate.file_id)
    if not file_bytes:
        return None, []

    parsed = await parse_order_confirmation(
        file_bytes,
        candidate.name,
        candidate.mime_type,
        api_key=settings.anthropic_api_key,
    )
    confirmation = await save_confirmation(
        db, org_id, candidate.file_id, candidate.modified_time, candidate.name, parsed
    )
    await db.flush()
    return confirmation, list(confirmation.lines)


async def _match_order_confirmations(db, imp, products: list[dict], sid: str) -> dict:
    """
    Step 4c — find each product's order confirmation in Drive and merge it in.

    Products are grouped by vendor + season + order number, since one Drive file
    covers one such group. Returns counts for the SSE summary.

    The caller treats a failure here as non-fatal: an import must still complete
    on invoice data alone if Drive is unreachable or nothing is found.
    """
    from app.services.drive_service import search_order_confirmations
    from app.services.order_matching import (
        ProductProxy,
        group_lines_by_match,
        match_products_to_order_lines,
        merge_with_order_data,
    )

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for p in products:
        vendor = (p.get("vendor") or "").strip()
        if not vendor:
            continue
        key = (
            vendor.casefold(),
            (p.get("season_normalized") or p.get("season") or "").strip(),
            (p.get("order_number") or "").strip(),
        )
        groups.setdefault(key, []).append(p)

    matched_total = 0
    confirmations_found = 0

    for (vendor_key, season, order_number), group in groups.items():
        vendor = (group[0].get("vendor") or "").strip()

        _emit_event(sid, {
            "type": "log",
            "message": f"Søger ordrebekræftelse for {vendor}...",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        })

        candidates = await search_order_confirmations(
            db,
            imp.organisation_id,
            vendor_name=vendor,
            order_number=order_number or None,
            season=season or None,
            skus=[p.get("style_code") for p in group if p.get("style_code")],
        )
        if not candidates:
            _emit_event(sid, {
                "type": "log",
                "message": f"Ingen ordrebekræftelse fundet for {vendor}",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            })
            continue

        confirmation, lines = await _load_order_confirmation_lines(
            db, imp.organisation_id, candidates[0]
        )
        if not lines:
            _emit_event(sid, {
                "type": "log",
                "message": f"Kunne ikke læse {candidates[0].name}",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            })
            continue

        confirmations_found += 1
        _emit_event(sid, {
            "type": "log",
            "message": f"Fandt {candidates[0].name} ({len(lines)} linjer)",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        })

        proxies = [ProductProxy(p) for p in group]
        matches = match_products_to_order_lines(
            proxies,
            lines,
            vendor=(confirmation.vendor if confirmation else None) or vendor,
            season=(confirmation.season if confirmation else None) or season or None,
        )
        grouped_lines = group_lines_by_match(matches, lines)
        by_id = {proxy.id: proxy for proxy in proxies}

        for match in matches:
            proxy = by_id.get(match.product_id)
            if proxy is None:
                continue
            merge_with_order_data(
                proxy, grouped_lines.get(match.product_id, []), match=match
            )

        matched_total += len(matches)
        _emit_event(sid, {
            "type": "log",
            "message": f"{len(matches)}/{len(group)} produkter matchet mod {vendor}",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        })

    return {
        "matched": matched_total,
        "total": len(products),
        "confirmations_found": confirmations_found,
    }


def _derive_name_from_filename(filename: str) -> str:
    """Derive a human-readable import name from a filename.

    Example: "bytom-aw24-pre.pdf" → "Bytom AW24 Pre"
    """
    base = os.path.splitext(filename)[0]
    # Replace common separators with spaces
    for sep in ("-", "_"):
        base = base.replace(sep, " ")
    return base.title()


# ---------------------------------------------------------------------------
# POST /  — Upload one or more PDF files
# ---------------------------------------------------------------------------
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_import(
    files: list[UploadFile] = File(...),
    name: str = Form(""),
    eur_rate: float = Form(7.46),
    markup: float = Form(2.5),
    test_mode: bool = Form(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more PDF invoices and create a new import record."""
    if eur_rate <= 0:
        raise HTTPException(status_code=400, detail="EUR-kurs skal være større end 0")
    if not files:
        raise HTTPException(status_code=400, detail="Mindst én fil er påkrævet")

    # ----- Validate all files first -----
    file_contents: list[tuple[UploadFile, bytes]] = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Kun PDF-filer accepteres ('{f.filename}')",
            )

        header = await f.read(5)
        await f.seek(0)
        if header != b"%PDF-":
            raise HTTPException(
                status_code=400,
                detail=f"Filen '{f.filename}' er ikke en gyldig PDF",
            )

        content = await f.read()
        size_mb = len(content) / (1024 * 1024)
        if size_mb > settings.max_upload_size_mb:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Filen '{f.filename}' er for stor ({size_mb:.1f} MB). "
                    f"Maks er {settings.max_upload_size_mb} MB."
                ),
            )
        file_contents.append((f, content))

    # ----- Derive import name -----
    import_name = name.strip()
    if not import_name:
        import_name = _derive_name_from_filename(files[0].filename)

    # ----- Save files to disk & create DB records -----
    upload_dir = os.path.join(settings.upload_dir, str(user.organisation_id))
    os.makedirs(upload_dir, mode=0o750, exist_ok=True)

    # Use first file's info for backward-compatible fields on Import
    first_file = file_contents[0]
    first_file_id = str(uuid.uuid4())
    first_file_path = os.path.join(upload_dir, f"{first_file_id}.pdf")

    import_record = Import(
        organisation_id=user.organisation_id,
        created_by_id=user.id,
        name=import_name,
        is_test=test_mode,
        status="uploading",
        file_name=first_file[0].filename,
        file_path=first_file_path,
        file_size_bytes=len(first_file[1]),
        eur_rate=eur_rate,
        markup=markup,
    )
    db.add(import_record)
    await db.flush()

    import_file_ids: list[str] = []

    for upload_file, content in file_contents:
        file_id = str(uuid.uuid4())
        file_path = os.path.join(upload_dir, f"{file_id}.pdf")
        with open(file_path, "wb") as fh:
            fh.write(content)

        import_file = ImportFile(
            import_id=import_record.id,
            file_name=upload_file.filename,
            file_path=file_path,
            file_size_bytes=len(content),
            status="uploaded",
        )
        db.add(import_file)
        await db.flush()
        import_file_ids.append(str(import_file.id))

    # Update first_file_path to the actual saved path for backward compat
    import_record.file_path = first_file_path
    import_record.status = "uploaded"
    await db.flush()

    return {
        "id": str(import_record.id),
        "name": import_record.name,
        "status": import_record.status,
        "file_ids": import_file_ids,
        "file_count": len(import_file_ids),
        "created_at": import_record.created_at.isoformat() if import_record.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /  — List imports
# ---------------------------------------------------------------------------
@router.get("/")
async def list_imports(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 50,
):
    """List imports for the current org with pagination."""
    # Clamp limit to prevent abuse
    limit = min(limit, 100)

    result = await db.execute(
        select(Import)
        .where(Import.organisation_id == user.organisation_id)
        .order_by(Import.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    imports = result.scalars().all()

    if not imports:
        return []

    # Get file counts in a single query (avoids N+1)
    import_ids = [imp.id for imp in imports]
    file_counts_result = await db.execute(
        select(ImportFile.import_id, func.count(ImportFile.id))
        .where(ImportFile.import_id.in_(import_ids))
        .group_by(ImportFile.import_id)
    )
    file_counts = dict(file_counts_result.all())

    return [
        {
            "id": str(imp.id),
            "name": imp.name,
            "is_test": imp.is_test,
            "status": imp.status,
            "file_name": imp.file_name,
            "file_count": file_counts.get(imp.id, 0),
            "total_products": imp.total_products,
            "products_pushed": imp.products_pushed,
            "created_at": imp.created_at.isoformat() if imp.created_at else None,
        }
        for imp in imports
    ]


# ---------------------------------------------------------------------------
# GET /{import_id}  — Single import detail with files and products
# ---------------------------------------------------------------------------
@router.get("/{import_id}")
async def get_import(
    import_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single import with its files and products."""
    try:
        import_uuid = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")
    result = await db.execute(
        select(Import).where(
            Import.id == import_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    imp = result.scalar_one_or_none()
    if not imp:
        raise HTTPException(status_code=404, detail="Import ikke fundet")

    # Load files
    files_result = await db.execute(
        select(ImportFile)
        .where(ImportFile.import_id == imp.id)
        .order_by(ImportFile.created_at)
    )
    import_files = files_result.scalars().all()

    # Load products
    products_result = await db.execute(
        select(ImportProduct)
        .where(ImportProduct.import_id == imp.id)
        .order_by(ImportProduct.created_at)
    )
    products = products_result.scalars().all()

    # Load uploaded images for all products in this import
    product_ids = [p.id for p in products]
    uploaded_images_map: dict[str, list[dict]] = {}
    if product_ids:
        from app.core.config import get_settings as _get_settings
        _settings = _get_settings()
        base_url = _settings.public_base_url.rstrip("/")

        images_result = await db.execute(
            select(ProductImage)
            .where(ProductImage.product_id.in_(product_ids))
            .order_by(ProductImage.sort_order)
        )
        for img in images_result.scalars().all():
            pid = str(img.product_id)
            if pid not in uploaded_images_map:
                uploaded_images_map[pid] = []
            uploaded_images_map[pid].append({
                "id": str(img.id),
                "filename": img.filename,
                "url": f"{base_url}/api/v1/images/{img.file_path}",
                "source": "uploaded",
                "sort_order": img.sort_order,
            })

    # How many distinct order confirmations back this import. Counting the
    # linked line ids would count sizes, not documents, so resolve the lines to
    # their parent confirmations.
    linked_line_ids = [
        p.order_confirmation_line_id for p in products if p.order_confirmation_line_id
    ]
    order_confirmations_found = 0
    if linked_line_ids:
        oc_result = await db.execute(
            select(func.count(func.distinct(OrderConfirmationLine.order_confirmation_id)))
            .where(OrderConfirmationLine.id.in_(linked_line_ids))
        )
        order_confirmations_found = oc_result.scalar() or 0

    return {
        "id": str(imp.id),
        "name": imp.name,
        "is_test": imp.is_test,
        "status": imp.status,
        "file_name": imp.file_name,
        "file_count": len(import_files),
        "total_products": imp.total_products,
        "products_pushed": imp.products_pushed,
        "eur_rate": imp.eur_rate,
        "markup": imp.markup,
        "invoice_number": imp.invoice_number,
        "invoice_date": imp.invoice_date.isoformat() if imp.invoice_date else None,
        # Order confirmation coverage — how much of this import was verified
        # against an order confirmation rather than resting on invoice data.
        "matched_count": sum(1 for p in products if p.order_confirmation_line_id),
        "unmatched_count": sum(1 for p in products if not p.order_confirmation_line_id),
        "order_confirmations_found": order_confirmations_found,
        "error_message": imp.error_message,
        "created_at": imp.created_at.isoformat() if imp.created_at else None,
        "completed_at": imp.completed_at.isoformat() if imp.completed_at else None,
        "files": [
            {
                "id": str(f.id),
                "file_name": f.file_name,
                "file_size_bytes": f.file_size_bytes,
                "status": f.status,
                "products_found": f.products_found,
                "error_message": f.error_message,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in import_files
        ],
        "products": [
            {
                "id": str(p.id),
                "title": p.title,
                "vendor": p.vendor,
                "product_type": p.product_type,
                "description_da": p.description_da,
                "description_en": p.description_en or "",
                "style_code": p.style_code,
                "color": p.color,
                "cost_price_eur": p.cost_price_eur,
                "gross_price_eur": p.gross_price_eur,
                "discount_pct": p.discount_pct or 0,
                "retail_price_dkk": p.retail_price_dkk,
                "variants": p.variants,
                "images": p.images,
                "uploaded_images": uploaded_images_map.get(str(p.id), []),
                "status": p.status,
                "shopify_product_id": p.shopify_product_id,
                "is_restock": p.is_restock,
                "shopify_match_id": p.shopify_match_id,
                "shopify_match_title": p.shopify_match_title,
                "duplicate_of_import_id": str(p.duplicate_of_import_id) if p.duplicate_of_import_id else None,
                "duplicate_import_date": p.duplicate_import_date,
                "seo_keywords": p.seo_keywords or [],
                "qa_warnings": p.qa_warnings or [],
                "order_number": p.order_number,
                "invoice_number": p.invoice_number,
                "season_raw": p.season_raw,
                "season_normalized": p.season_normalized,
                "order_confirmation_line_id": (
                    str(p.order_confirmation_line_id) if p.order_confirmation_line_id else None
                ),
                "match_confidence": p.match_confidence,
                "match_method": p.match_method,
                "data_sources": p.data_sources or {},
            }
            for p in products
        ],
    }


# ---------------------------------------------------------------------------
# GET /{import_id}/stream  — SSE streaming endpoint
# ---------------------------------------------------------------------------
@router.get("/{import_id}/stream")
async def stream_analysis(
    import_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream analysis progress via Server-Sent Events."""
    # Verify access
    try:
        import_uuid = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")
    result = await db.execute(
        select(Import).where(
            Import.id == import_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    imp = result.scalar_one_or_none()
    if not imp:
        raise HTTPException(status_code=404, detail="Import ikke fundet")

    return StreamingResponse(
        _sse_generator(import_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_generator(import_id: str):
    """Yield SSE events as they become available."""
    last_idx = 0
    while True:
        events = _analysis_progress.get(import_id, [])
        for event in events[last_idx:]:
            yield f"data: {json.dumps(event)}\n\n"
            last_idx += 1

        # Check if done or errored
        if events and events[-1].get("type") in ("done", "error"):
            break

        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# POST /{import_id}/analyse  — Trigger analysis
# ---------------------------------------------------------------------------
@router.post("/{import_id}/analyse")
async def trigger_analysis(
    request: Request,
    import_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger AI analysis of an uploaded import. Rate-limited: expensive Claude Vision calls."""
    try:
        import_uuid = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")
    result = await db.execute(
        select(Import).where(
            Import.id == import_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    imp = result.scalar_one_or_none()
    if not imp:
        raise HTTPException(status_code=404, detail="Import ikke fundet")

    if imp.status not in ("uploaded", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Kan ikke analysere import med status '{imp.status}'",
        )

    imp.status = "analysing"
    await db.commit()

    # Run analysis in background
    background_tasks.add_task(_run_analysis, imp.id)

    return {"id": str(imp.id), "status": imp.status}


# ---------------------------------------------------------------------------
# Background analysis — multi-file
# ---------------------------------------------------------------------------
async def _run_analysis(import_id: uuid.UUID):
    """Background task: parse all PDF files, extract products with AI, find images."""
    settings = get_settings()
    sid = str(import_id)

    # Clean up any stale progress entries
    _cleanup_stale_progress()

    async with async_session() as db:
        try:
            result = await db.execute(select(Import).where(Import.id == import_id))
            imp = result.scalar_one()

            if imp.status != "analysing":
                logger.warning(f"Import {import_id} is not in 'analysing' status, skipping")
                return

            # Load all files for this import
            files_result = await db.execute(
                select(ImportFile)
                .where(ImportFile.import_id == import_id)
                .order_by(ImportFile.created_at)
            )
            import_files = files_result.scalars().all()

            # If no ImportFile records exist (legacy single-file import), create a
            # virtual list from the Import record itself.
            if not import_files:
                import_files_data = [
                    {
                        "id": None,
                        "file_name": imp.file_name,
                        "file_path": imp.file_path,
                    }
                ]
            else:
                import_files_data = [
                    {
                        "id": f.id,
                        "file_name": f.file_name,
                        "file_path": f.file_path,
                        "model": f,
                    }
                    for f in import_files
                ]

            total_files = len(import_files_data)
            all_products: list[dict] = []
            order_confirmations_found = 0

            # Pre-load brand extraction examples for few-shot AI prompting
            _brand_extraction_examples: dict[str, list[dict]] = {}
            try:
                brands_result = await db.execute(
                    select(Brand.name, Brand.extraction_examples).where(
                        Brand.organisation_id == imp.organisation_id,
                        Brand.is_active == True,
                        Brand.extraction_examples.isnot(None),
                    )
                )
                for row in brands_result.all():
                    if row.extraction_examples:
                        _brand_extraction_examples[row.name] = row.extraction_examples
                if _brand_extraction_examples:
                    logger.info(
                        f"Loaded extraction examples for {len(_brand_extraction_examples)} brands: "
                        f"{list(_brand_extraction_examples.keys())}"
                    )
            except Exception as ex_load_err:
                logger.warning(f"Failed to load brand extraction examples (non-critical): {ex_load_err}")

            # Pre-load active product descriptions from Shopify for AI reference (Opt 6)
            _active_descriptions: list[dict] | None = None
            try:
                from app.models.shopify_connection import ShopifyConnection as _SC
                from app.core.security import decrypt_token as _dt

                _conn_res = await db.execute(
                    select(_SC).where(
                        _SC.organisation_id == imp.organisation_id,
                        _SC.is_active == True,
                    )
                )
                _shopify_conn = _conn_res.scalar_one_or_none()
                if _shopify_conn:
                    from app.api.routes.shopify import ShopifyGraphQL as _SG
                    _at = _dt(_shopify_conn.access_token_encrypted)
                    _shopify_ref = _SG(_shopify_conn.shop_domain, _at)
                    _active_descriptions = await asyncio.to_thread(
                        _shopify_ref.fetch_recent_products, 10
                    )
                    if _active_descriptions:
                        logger.info(
                            f"Loaded {len(_active_descriptions)} active product descriptions for AI reference"
                        )
            except Exception as _desc_err:
                logger.warning(f"Failed to load active descriptions (non-critical): {_desc_err}")

            for file_idx, file_info in enumerate(import_files_data, start=1):
                fname = file_info["file_name"]
                fpath = file_info["file_path"]
                file_model = file_info.get("model")

                _emit_event(sid, {
                    "type": "file_start",
                    "file_name": fname,
                    "file_index": file_idx,
                    "total_files": total_files,
                })

                # Update file status
                if file_model:
                    file_model.status = "parsing"
                    await db.flush()

                try:
                    _emit_event(sid, {
                        "type": "log",
                        "message": f"Claude Vision \u2013 analyserer {fname}",
                        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    })

                    # 1. Read PDF file
                    with open(fpath, "rb") as fh:
                        pdf_bytes = fh.read()

                    # 2. Extract text + images from PDF
                    pdf_text = await asyncio.to_thread(extract_pdf_text, pdf_bytes)
                    pdf_images = await asyncio.to_thread(extract_pdf_pages_as_images, pdf_bytes)

                    # 3. Parse tables
                    table_products = await asyncio.to_thread(parse_invoice_tables, pdf_bytes)

                    # 3b. Invoice header: order/invoice number, date, season.
                    # Read separately from the tables so it is still available
                    # when this invoice has no parseable product table.
                    invoice_meta = await asyncio.to_thread(parse_invoice_metadata, pdf_bytes)

                    _emit_event(sid, {
                        "type": "log",
                        "message": f"Fundet tabel med {len(table_products)} produktrækker" if table_products else "Ingen tabeldata fundet, bruger AI-ekstraktion",
                        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    })

                    # 4. AI extraction with Claude Vision
                    # Load historical keyword data for AI prompt (Layer 2)
                    _historical_keywords = None
                    try:
                        from app.services.search_console_service import get_historical_keywords
                        _historical_keywords = await get_historical_keywords(
                            db, imp.organisation_id
                        )
                    except ImportError:
                        pass  # search_console_service not yet deployed
                    except Exception:
                        pass  # Non-critical — AI works fine without historical data

                    ai_products = await extract_products_with_ai(
                        pdf_text=pdf_text,
                        existing_tags=[],
                        pdf_images=pdf_images,
                        table_products=table_products,
                        active_descriptions=_active_descriptions,
                        brand_extraction_examples=_brand_extraction_examples or None,
                        historical_keywords=_historical_keywords,
                        api_key=settings.anthropic_api_key,
                        eur_to_dkk=imp.eur_rate,
                        markup=imp.markup,
                    )

                    _emit_event(sid, {
                        "type": "log",
                        "message": f"AI fandt {len(ai_products)} produkter i {fname}",
                        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    })

                    # 4a. Backfill order/invoice/season from the invoice header for
                    # products the deterministic parser never saw. Values already
                    # present (parser first, then AI) are left alone.
                    for p in ai_products:
                        for meta_key in ("order_number", "invoice_number", "invoice_date"):
                            if invoice_meta.get(meta_key) and not p.get(meta_key):
                                p[meta_key] = invoice_meta[meta_key]
                        if invoice_meta.get("season_raw") and not p.get("season_raw"):
                            p["season_raw"] = invoice_meta["season_raw"]
                            p["season_normalized"] = normalize_season(invoice_meta["season_raw"])

                    if invoice_meta.get("invoice_number") and not imp.invoice_number:
                        imp.invoice_number = invoice_meta["invoice_number"][:100]
                    if invoice_meta.get("invoice_date") and not imp.invoice_date:
                        try:
                            imp.invoice_date = date.fromisoformat(invoice_meta["invoice_date"])
                        except ValueError:
                            logger.warning(
                                f"Unparseable invoice date {invoice_meta['invoice_date']!r} in {fname}"
                            )

                    # 4b. Lookup brand-specific config (markup + image scraping) for all vendors
                    # Resolve the Drive token once per file rather than per brand:
                    # the image search runs in a worker thread and cannot await a
                    # token refresh, so it is handed a ready one.
                    _drive_access_token = ""
                    try:
                        from app.services.drive_service import get_valid_access_token as _drive_token
                        _drive_access_token = await _drive_token(db, imp.organisation_id) or ""
                    except Exception as drive_token_err:
                        logger.warning(f"Could not resolve Drive token: {drive_token_err}")

                    unique_vendors = {p.get("vendor", "").strip() for p in ai_products if p.get("vendor")}
                    vendor_markup_map: dict[str, float] = {}
                    vendor_image_config: dict[str, dict] = {}  # vendor_lower → {website_url, search_url_pattern}
                    if unique_vendors:
                        try:
                            for vendor_name in unique_vendors:
                                brand_result = await db.execute(
                                    select(
                                        Brand.markup, Brand.name,
                                        Brand.website_url, Brand.search_url_pattern,
                                        Brand.image_bank_url, Brand.image_bank_type,
                                        Brand.image_bank_search_pattern,
                                        Brand.drive_folder_id,
                                    ).where(
                                        Brand.organisation_id == imp.organisation_id,
                                        Brand.is_active == True,
                                        func.lower(Brand.name) == vendor_name.lower(),
                                    )
                                )
                                brand_row = brand_result.first()
                                if brand_row:
                                    if brand_row.markup:
                                        vendor_markup_map[vendor_name.lower()] = brand_row.markup
                                        logger.info(
                                            f"Brand markup for '{vendor_name}': {brand_row.markup}x"
                                        )
                                    # Always store image config if any URL is set
                                    has_img_config = (
                                        brand_row.website_url
                                        or brand_row.search_url_pattern
                                        or brand_row.image_bank_url
                                        or brand_row.image_bank_search_pattern
                                        or brand_row.drive_folder_id
                                    )
                                    if has_img_config:
                                        vendor_image_config[vendor_name.lower()] = {
                                            "website_url": brand_row.website_url or "",
                                            "search_url_pattern": brand_row.search_url_pattern or "",
                                            "image_bank_url": brand_row.image_bank_url or "",
                                            "image_bank_type": brand_row.image_bank_type or "",
                                            "image_bank_search_pattern": brand_row.image_bank_search_pattern or "",
                                            "drive_folder_id": brand_row.drive_folder_id or "",
                                            # Resolved once per import below —
                                            # the image search runs in a worker
                                            # thread and cannot await a refresh.
                                            "drive_access_token": _drive_access_token or "",
                                        }
                                        logger.info(
                                            f"Brand image config for '{vendor_name}': "
                                            f"website={brand_row.website_url or 'none'}, "
                                            f"search_pattern={'yes' if brand_row.search_url_pattern else 'none'}, "
                                            f"image_bank={'yes' if brand_row.image_bank_search_pattern else 'none'}, "
                                            f"drive_folder={'yes' if brand_row.drive_folder_id else 'none'}"
                                        )
                        except Exception as brand_err:
                            logger.warning(f"Brand config lookup failed: {brand_err}")

                    # 4c. Order confirmation matching — the only source of RRP.
                    # Deliberately non-fatal: if Drive is unreachable, the file
                    # is missing or parsing fails, the import must still finish
                    # on invoice data alone. Products simply stay unmatched.
                    try:
                        match_stats = await _match_order_confirmations(
                            db, imp, ai_products, sid
                        )
                        order_confirmations_found += match_stats["confirmations_found"]
                    except Exception as oc_err:
                        logger.warning(
                            f"Order confirmation matching failed for {fname}: {oc_err}",
                            exc_info=True,
                        )
                        _emit_event(sid, {
                            "type": "log",
                            "message": (
                                "Ordrebekræftelses-matching sprang over "
                                "— fortsætter med fakturadata"
                            ),
                            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        })

                    # 5. Enrich each product — split into sync enrichment + parallel image search

                    # ── Pass A: Synchronous enrichment (instant, no I/O) ──
                    for p in ai_products:
                        cost_eur = p.get("cost_price_eur", 0) or 0
                        if cost_eur > 0 and (imp.eur_rate or 0) > 0:
                            vendor_lower = (p.get("vendor") or "").strip().lower()
                            product_markup = vendor_markup_map.get(vendor_lower, imp.markup)
                            p["retail_price_dkk"] = calculate_retail_price(
                                cost_eur, imp.eur_rate, product_markup
                            )

                        raw_type = p.get("product_type") or p.get("type") or ""
                        if raw_type:
                            p["product_type"] = map_type_danish(raw_type)

                        if not p.get("description_da"):
                            p["description_da"] = build_description_da(p)

                        if not p.get("_description_en_scraped"):
                            p["_description_en_scraped"] = ""

                        if p.get("variants"):
                            p["variants"] = sort_sizes(p["variants"])

                        # Attach image bank shortcut URL for review UI
                        _v_lower = (p.get("vendor") or "").strip().lower()
                        _ib_cfg = vendor_image_config.get(_v_lower, {})
                        _ib_pattern = _ib_cfg.get("image_bank_search_pattern", "")
                        _ib_url = _ib_cfg.get("image_bank_url", "")
                        _sku = p.get("style_code", "")
                        if _ib_pattern and "{sku}" in _ib_pattern and _sku:
                            from urllib.parse import quote as _url_quote
                            p["image_bank_direct_url"] = _ib_pattern.replace(
                                "{sku}", _url_quote(_sku)
                            )
                        elif _ib_url:
                            p["image_bank_direct_url"] = _ib_url

                    # ── Pass B: Parallel image search + verification ──
                    # All products' image pipelines run concurrently with a
                    # semaphore to cap simultaneous HTTP/API calls.
                    _IMG_CONCURRENCY = 5  # max parallel image tasks

                    async def _enrich_images_for_product(
                        p: dict,
                        sem: asyncio.Semaphore,
                        vendor_image_config: dict,
                        sid: str,
                        org_id,
                    ) -> None:
                        """Search + verify images for one product (runs concurrently)."""
                        async with sem:
                            try:
                                _vendor = p.get("vendor", "")
                                _style_code = p.get("style_code", "")

                                # ── Cache lookup: reuse previously verified images ──
                                if _vendor and _style_code:
                                    try:
                                        cached = await get_cached_images(
                                            org_id=org_id,
                                            vendor=_vendor,
                                            style_code=_style_code,
                                        )
                                        if cached:
                                            p["images"] = cached["images"]
                                            p["image_source"] = cached.get("image_source", "cache")
                                            details = cached.get("details", {})
                                            if details.get("description_en"):
                                                p["_description_en_scraped"] = details["description_en"]
                                            logger.info(
                                                f"[IMG-CACHE] Cache hit for '{_vendor}' / '{_style_code}' "
                                                f"({cached.get('hit_count', '?')} uses)"
                                            )
                                            return
                                    except Exception as cache_err:
                                        logger.warning(
                                            f"[IMG-CACHE] Cache lookup failed for '{_vendor}' / '{_style_code}': {cache_err}"
                                        )

                                # ── Cache miss: normal scraping pipeline ──
                                _vendor_lower = _vendor.strip().lower()
                                _brand_img_config = vendor_image_config.get(_vendor_lower)
                                image_result = await asyncio.to_thread(
                                    find_product_images_and_details,
                                    vendor=_vendor,
                                    style_code=_style_code,
                                    title=p.get("title", ""),
                                    brand_config=_brand_img_config,
                                )
                                scraped_images = image_result.get("images", [])
                                image_source = image_result.get("image_source", "none")
                                details = image_result.get("details", {})
                                if details.get("description_en"):
                                    p["_description_en_scraped"] = details["description_en"]

                                if scraped_images:
                                    try:
                                        verified_images = await verify_and_filter_images(
                                            image_urls=scraped_images,
                                            product_title=p.get("title", ""),
                                            vendor=_vendor,
                                            style_code=_style_code,
                                            color=p.get("color", ""),
                                            product_type=p.get("product_type", ""),
                                        )
                                        if verified_images:
                                            p["images"] = verified_images
                                            p["image_source"] = image_source
                                            logger.info(
                                                f"[IMG] Verified {len(verified_images)}/{len(scraped_images)} "
                                                f"images for '{p.get('title')}' (source: {image_source})"
                                            )
                                            # ── Save to cache after successful verification ──
                                            try:
                                                await save_image_cache(
                                                    org_id=org_id,
                                                    vendor=_vendor,
                                                    style_code=_style_code,
                                                    image_result={
                                                        "images": verified_images,
                                                        "image_source": image_source,
                                                        "product_page_url": image_result.get("product_page_url", ""),
                                                        "details": details,
                                                    },
                                                )
                                            except Exception as save_err:
                                                logger.warning(
                                                    f"[IMG-CACHE] Failed to save cache for '{_vendor}' / '{_style_code}': {save_err}"
                                                )
                                        else:
                                            p["images"] = []
                                            p["image_source"] = f"rejected:{image_source}"
                                            logger.warning(
                                                f"[IMG] Vision rejected ALL {len(scraped_images)} images "
                                                f"for '{p.get('title')}' (source: {image_source}) — likely wrong product"
                                            )
                                            _emit_event(sid, {
                                                "type": "log",
                                                "message": f"⚠ Billeder afvist (forkert produkt): {p.get('title', 'Ukendt')}",
                                            })
                                    except Exception as verify_err:
                                        logger.warning(
                                            f"[IMG] Vision verification failed for '{p.get('title')}': {verify_err}. "
                                            f"Using NO images (safety first)."
                                        )
                                        p["images"] = []
                                        p["image_source"] = f"verify_error:{image_source}"
                                        _emit_event(sid, {
                                            "type": "log",
                                            "message": f"⚠ Billedverificering fejlede — ingen billeder: {p.get('title', 'Ukendt')}",
                                        })
                                else:
                                    p["images"] = []
                                    p["image_source"] = "none"

                                if not p["images"]:
                                    logger.warning(
                                        f"No images found for '{p.get('title')}' "
                                        f"(SKU: {p.get('style_code')}, vendor: {p.get('vendor')})"
                                    )
                                    _emit_event(sid, {
                                        "type": "log",
                                        "message": f"⚠ Ingen billeder fundet: {p.get('title', 'Ukendt')}",
                                    })
                            except Exception as img_err:
                                logger.error(
                                    f"Image search failed for '{p.get('title')}' "
                                    f"(SKU: {p.get('style_code')}): {img_err}"
                                )
                                p["images"] = []
                                p["image_source"] = "error"

                    _emit_event(sid, {
                        "type": "log",
                        "message": f"Søger billeder for {len(ai_products)} produkter (parallel)...",
                        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    })

                    img_sem = asyncio.Semaphore(_IMG_CONCURRENCY)
                    img_tasks = [
                        _enrich_images_for_product(p, img_sem, vendor_image_config, sid, org_id=imp.organisation_id)
                        for p in ai_products
                    ]
                    await asyncio.gather(*img_tasks)

                    # Update file status
                    if file_model:
                        file_model.status = "parsed"
                        file_model.products_found = len(ai_products)
                        await db.flush()

                    _emit_event(sid, {
                        "type": "file_done",
                        "file_name": fname,
                        "products_found": len(ai_products),
                    })

                    all_products.extend(ai_products)

                except Exception as file_err:
                    logger.error(f"Error processing file {fname}: {file_err}", exc_info=True)
                    if file_model:
                        file_model.status = "error"
                        file_model.error_message = str(file_err)[:1000]
                        await db.flush()

                    _emit_event(sid, {
                        "type": "error",
                        "message": f"Fejl ved behandling af {fname}: {str(file_err)[:200]}",
                    })
                    # Continue with remaining files
                    continue

                # Emit progress
                percent = int((file_idx / total_files) * 100)
                _emit_event(sid, {
                    "type": "progress",
                    "percent": percent,
                    "current_file": file_idx,
                    "total_files": total_files,
                })

            # 5a-merge. Deduplicate products within this import.
            # If the same SKU+vendor appears from multiple PDF files, merge their variants.
            # This prevents duplicates when e.g. the same invoice is split across pages.
            original_count = len(all_products)
            if original_count > 1:
                seen: dict[str, int] = {}  # dedup_key → index in deduped list
                deduped: list[dict] = []

                for p in all_products:
                    sku = (p.get("style_code") or "").strip().lower()
                    vendor = (p.get("vendor") or "").strip().lower()
                    color = (p.get("color_original") or p.get("color") or "").strip().lower()

                    # Products without SKU can't be deduped — always keep
                    if not sku:
                        deduped.append(p)
                        continue

                    # Include color in dedup key: same SKU in different colors = separate products
                    dedup_key = f"{vendor}|{sku}|{color}"

                    if dedup_key not in seen:
                        seen[dedup_key] = len(deduped)
                        deduped.append(p)
                    else:
                        # Merge into the first occurrence
                        existing = deduped[seen[dedup_key]]

                        # Merge variants: sum quantities for same size, add new sizes
                        size_map: dict[str, dict] = {}
                        for v in (existing.get("variants") or []):
                            size_map[(v.get("size") or "").strip().lower()] = v
                        for v in (p.get("variants") or []):
                            sk = (v.get("size") or "").strip().lower()
                            if sk in size_map:
                                size_map[sk]["quantity"] = (
                                    (size_map[sk].get("quantity") or 0)
                                    + (v.get("quantity") or 0)
                                )
                                if not size_map[sk].get("ean") and v.get("ean"):
                                    size_map[sk]["ean"] = v["ean"]
                            else:
                                size_map[sk] = v
                        existing["variants"] = list(size_map.values())

                        # Fill in any missing fields from the duplicate
                        for field in ["title", "description_da", "material", "color",
                                      "gender", "season", "country_of_origin", "hs_code"]:
                            if not existing.get(field) and p.get(field):
                                existing[field] = p[field]

                        # Merge images (deduplicate by URL)
                        existing_imgs = list(existing.get("images") or [])
                        existing_img_set = set(existing_imgs)
                        for img in (p.get("images") or []):
                            if img not in existing_img_set:
                                existing_imgs.append(img)
                                existing_img_set.add(img)
                        existing["images"] = existing_imgs

                        logger.info(
                            f"[DEDUP] Merged duplicate SKU '{p.get('style_code')}' "
                            f"({p.get('vendor')}) — now {len(existing['variants'])} variants"
                        )

                all_products = deduped

                merged_count = original_count - len(all_products)
                if merged_count > 0:
                    _emit_event(sid, {
                        "type": "log",
                        "message": (
                            f"Sammenflettet {merged_count} duplikerede produkt(er) "
                            f"— {len(all_products)} unikke produkter"
                        ),
                        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    })

            # 5a-cross. Check for duplicate SKUs in previous imports (advisory warning only).
            # This never blocks anything — just sets a warning flag for the review UI.
            try:
                skus_to_check = [
                    (p.get("style_code") or "").strip()
                    for p in all_products
                    if (p.get("style_code") or "").strip()
                ]
                if skus_to_check:
                    from sqlalchemy import and_
                    # Join through Import to scope to same organisation
                    prev_result = await db.execute(
                        select(
                            ImportProduct.style_code,
                            ImportProduct.import_id,
                            ImportProduct.created_at,
                        )
                        .join(Import, Import.id == ImportProduct.import_id)
                        .where(
                            and_(
                                Import.organisation_id == imp.organisation_id,
                                ImportProduct.import_id != imp.id,
                                ImportProduct.style_code.in_(skus_to_check),
                                ImportProduct.status.in_(["pending", "approved", "pushed"]),
                            )
                        ).order_by(ImportProduct.created_at.desc())
                    )
                    prev_rows = prev_result.all()

                    # Build a map: style_code → (import_id, date) of most recent previous import
                    prev_map: dict[str, tuple] = {}
                    for row in prev_rows:
                        sc = (row.style_code or "").strip().lower()
                        if sc not in prev_map:
                            prev_map[sc] = (
                                row.import_id,
                                row.created_at.strftime("%d/%m-%Y") if row.created_at else "",
                            )

                    # Annotate products with duplicate warnings
                    dup_count = 0
                    for p in all_products:
                        sc = (p.get("style_code") or "").strip().lower()
                        if sc in prev_map:
                            p["_duplicate_of_import_id"] = prev_map[sc][0]
                            p["_duplicate_import_date"] = prev_map[sc][1]
                            dup_count += 1

                    if dup_count > 0:
                        _emit_event(sid, {
                            "type": "log",
                            "message": (
                                f"ℹ {dup_count} produkt(er) fundet i tidligere imports "
                                f"— vist som advarsel i review"
                            ),
                            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        })
            except Exception as dup_err:
                logger.warning(f"Cross-import duplicate check failed (non-critical): {dup_err}")

            # 5b. Check for existing products in Shopify (restock detection)
            if imp.is_test:
                _emit_event(sid, {
                    "type": "log",
                    "message": "Test mode — springer supplerings-tjek over",
                    "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                })
            else:
                try:
                    from app.models.shopify_connection import ShopifyConnection
                    from app.core.security import decrypt_token

                    conn_result = await db.execute(
                        select(ShopifyConnection).where(
                            ShopifyConnection.organisation_id == imp.organisation_id,
                            ShopifyConnection.is_active == True,
                        )
                    )
                    shopify_conn = conn_result.scalar_one_or_none()

                    if shopify_conn:
                        from app.api.routes.shopify import ShopifyGraphQL, _search_product_by_sku

                        access_token = decrypt_token(shopify_conn.access_token_encrypted)
                        shopify = ShopifyGraphQL(shopify_conn.shop_domain, access_token)

                        _emit_event(sid, {
                            "type": "log",
                            "message": "Tjekker for eksisterende produkter i Shopify (supplering)...",
                            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        })

                        for p in all_products:
                            style_code = p.get("style_code", "").strip()
                            if style_code:
                                match = await _search_product_by_sku(shopify, style_code)
                                if match and match.get("product_id"):
                                    # Verify the match is actually the same product (not just same SKU in different color).
                                    # Build expected handle and compare with Shopify match handle.
                                    p_color = (p.get("color") or "").lower().strip()
                                    match_handle = (match.get("handle") or "").lower()
                                    # If product has a color and the Shopify match handle does NOT contain that color,
                                    # it's a different color variant — NOT a restock.
                                    color_mismatch = False
                                    if p_color and match_handle:
                                        color_slug = re.sub(r"[^a-z0-9]+", "-", p_color).strip("-")
                                        if color_slug and color_slug not in match_handle:
                                            color_mismatch = True
                                            logger.info(
                                                f"SKU match but color mismatch: '{p.get('title')}' ({p_color}) "
                                                f"≠ Shopify '{match['title']}' (handle: {match_handle}) — not restock"
                                            )

                                    if not color_mismatch:
                                        p["is_restock"] = True
                                        p["shopify_match_id"] = match["product_id"]
                                        p["shopify_match_title"] = match["title"]
                                        logger.info(
                                            f"Restock match: '{p.get('title')}' → '{match['title']}' ({match['product_id']})"
                                        )
                    else:
                        logger.info("No active Shopify connection — skipping restock check")
                except Exception as e:
                    logger.warning(f"Restock check failed (non-critical): {e}")

            # 5c. Validate and optimize SEO keywords via Google Autocomplete (Layer 1)
            try:
                from app.services.seo_keyword_service import validate_keywords_batch

                _emit_event(sid, {
                    "type": "log",
                    "message": "Validerer SEO-søgeord mod Google Autocomplete...",
                    "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                })

                # Load historical keyword data if available (Layer 2)
                historical_keywords = None
                try:
                    from app.services.search_console_service import get_historical_keywords
                    historical_keywords = await get_historical_keywords(
                        db, imp.organisation_id
                    )
                except ImportError:
                    pass  # search_console_service not yet available — skip Layer 2
                except Exception as hist_err:
                    logger.debug(f"Historical keywords unavailable (non-critical): {hist_err}")

                # Load org-level DataForSEO credentials if available
                dfs_org_creds = None
                try:
                    from app.services.dataforseo_service import get_org_credentials
                    dfs_org_creds = await get_org_credentials(db, imp.organisation_id)
                except Exception:
                    pass  # Non-critical — falls back to .env credentials

                all_products = await validate_keywords_batch(
                    all_products, historical_keywords=historical_keywords,
                    org_credentials=dfs_org_creds,
                )

                _emit_event(sid, {
                    "type": "log",
                    "message": "SEO-søgeord valideret og optimeret",
                    "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                })
            except Exception as seo_err:
                logger.warning(f"SEO keyword validation failed (non-critical): {seo_err}")

            # 5b. QA validation — run quality checks on all products
            from app.services.product_qa import validate_products
            validate_products(all_products, eur_rate=imp.eur_rate or 7.46)

            _emit_event(sid, {
                "type": "log",
                "message": "Kvalitetstjek gennemført",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            })

            # 6. Save products to database
            for p in all_products:
                import_product = ImportProduct(
                    import_id=imp.id,
                    title=p.get("title", "Unknown"),
                    vendor=p.get("vendor", ""),
                    product_type=p.get("product_type", p.get("type", "")),
                    description_da=p.get("description_da", ""),
                    style_code=p.get("style_code", ""),
                    color=p.get("color", ""),
                    color_code=p.get("color_code", ""),
                    material=p.get("material", ""),
                    gender=p.get("gender", ""),
                    season=p.get("season", ""),
                    season_raw=p.get("season_raw") or None,
                    season_normalized=p.get("season_normalized") or None,
                    order_number=(p.get("order_number") or None),
                    invoice_number=(p.get("invoice_number") or None),
                    country_of_origin=p.get("country_of_origin", ""),
                    hs_code=p.get("hs_code", ""),
                    description_en=p.get("details_en") or p.get("_description_en_scraped") or "",
                    ai_tags=p.get("ai_tags", []),
                    seo_keywords=p.get("seo_keywords", []),
                    color_original=p.get("color_original", ""),
                    handle=p.get("handle", ""),
                    image_source=p.get("image_source", ""),
                    cost_price_eur=p.get("cost_price_eur"),
                    cost_price_dkk=p.get("cost_price_dkk"),
                    retail_price_dkk=p.get("retail_price_dkk"),
                    discount_pct=p.get("discount_pct", 0),
                    gross_price_eur=p.get("gross_price_eur"),
                    variants=p.get("variants", []),
                    images=p.get("images", []),
                    status="pending",
                    is_restock=p.get("is_restock", False),
                    shopify_match_id=p.get("shopify_match_id"),
                    shopify_match_title=p.get("shopify_match_title"),
                    duplicate_of_import_id=p.get("_duplicate_of_import_id"),
                    duplicate_import_date=p.get("_duplicate_import_date"),
                    qa_warnings=p.get("qa_warnings", []),
                    order_confirmation_line_id=p.get("order_confirmation_line_id"),
                    match_confidence=p.get("match_confidence"),
                    match_method=p.get("match_method"),
                    data_sources=p.get("data_sources") or {},
                )
                db.add(import_product)

            # 7. Auto-detect brands — create new Brand records for unknown vendors
            try:
                unique_vendors = {p.get("vendor", "").strip() for p in all_products if p.get("vendor", "").strip()}
                if unique_vendors:
                    from app.api.routes.brands import _slugify
                    existing_result = await db.execute(
                        select(Brand.slug).where(
                            Brand.organisation_id == imp.organisation_id
                        )
                    )
                    existing_slugs = {row[0] for row in existing_result.all()}

                    # Load brand suggestions for auto-populating website/search config
                    from app.api.routes.brands import _load_suggestions
                    suggestions = _load_suggestions()
                    suggestion_lookup = {s["slug"]: s for s in suggestions} if suggestions else {}

                    for vendor_name in unique_vendors:
                        slug = _slugify(vendor_name)
                        if slug and slug not in existing_slugs:
                            # Try to match with pre-populated suggestion data
                            suggestion = suggestion_lookup.get(slug, {})
                            new_brand = Brand(
                                id=uuid.uuid4(),
                                organisation_id=imp.organisation_id,
                                name=vendor_name,
                                slug=slug,
                                website_url=suggestion.get("website"),
                                search_url_pattern=suggestion.get("search_url"),
                            )
                            db.add(new_brand)
                            existing_slugs.add(slug)
                            logger.info(f"Auto-created brand: '{vendor_name}' (slug: {slug})")

                    await db.flush()
            except Exception as brand_err:
                logger.warning(f"Auto-brand creation failed (non-critical): {brand_err}")

            # 8. Auto-save extraction examples for brand few-shot learning
            try:
                vendor_examples: dict[str, list[dict]] = {}
                for p in all_products:
                    vendor = (p.get("vendor") or "").strip()
                    if not vendor or not p.get("style_code") or not p.get("details"):
                        continue
                    vendor_lower = vendor.lower()
                    if vendor_lower not in vendor_examples:
                        vendor_examples[vendor_lower] = []
                    if len(vendor_examples[vendor_lower]) < 3:
                        vendor_examples[vendor_lower].append({
                            "style_code": p.get("style_code", ""),
                            "title": p.get("title", ""),
                            "product_type": p.get("product_type", ""),
                            "color": p.get("color", ""),
                            "color_original": p.get("color_original", ""),
                            "details": (p.get("details") or p.get("description_da") or "")[:300],
                        })

                if vendor_examples:
                    for vendor_lower, examples in vendor_examples.items():
                        brand_result = await db.execute(
                            select(Brand).where(
                                Brand.organisation_id == imp.organisation_id,
                                func.lower(Brand.name) == vendor_lower,
                            )
                        )
                        brand = brand_result.scalar_one_or_none()
                        if brand:
                            brand.extraction_examples = examples
                            logger.info(
                                f"Saved {len(examples)} extraction examples for brand '{brand.name}'"
                            )
                    await db.flush()
            except Exception as ex_err:
                logger.warning(f"Extraction examples save failed (non-critical): {ex_err}")
                # flush failure poisons the session — rollback before continuing
                await db.rollback()
                # Re-fetch the import object after rollback (detached from session)
                result = await db.execute(select(Import).where(Import.id == import_id))
                imp = result.scalar_one()

            imp.status = "review"
            imp.total_products = len(all_products)
            await db.commit()

            _emit_event(sid, {
                "type": "done",
                "total_products": len(all_products),
            })

        except Exception as e:
            logger.error(f"Analysis failed for import {import_id}: {e}", exc_info=True)
            await db.rollback()

            _emit_event(sid, {
                "type": "error",
                "message": f"Analyse fejlede: {str(e)[:200]}",
            })

            # Update import with error
            async with async_session() as error_db:
                result = await error_db.execute(
                    select(Import).where(Import.id == import_id)
                )
                imp = result.scalar_one()
                imp.status = "failed"
                imp.error_message = str(e)[:1000]
                await error_db.commit()


# ---------------------------------------------------------------------------
# POST /{import_id}/link-order-confirmation
# ---------------------------------------------------------------------------
class LinkOrderConfirmationRequest(BaseModel):
    drive_file_id: str = Field(..., min_length=1, max_length=255)


@router.post("/{import_id}/link-order-confirmation")
async def link_order_confirmation(
    import_id: str,
    body: LinkOrderConfirmationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Point an import at a specific order confirmation in Drive.

    The safety net for when the automatic search in step 4c found nothing or
    picked the wrong file: the user names the file, and it is downloaded,
    parsed, matched and merged exactly as the pipeline would have done.

    Only products that are not already matched are touched, so re-running this
    cannot undo a good automatic match. Products already pushed to Shopify are
    left alone.
    """
    from app.services.drive_service import get_file_metadata
    from app.services.order_matching import (
        group_lines_by_match,
        match_products_to_order_lines,
        merge_with_order_data,
    )

    try:
        import_uuid = uuid.UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID format")

    result = await db.execute(
        select(Import).where(
            Import.id == import_uuid,
            Import.organisation_id == user.organisation_id,
        )
    )
    imp = result.scalar_one_or_none()
    if not imp:
        raise HTTPException(status_code=404, detail="Import ikke fundet")

    metadata = await get_file_metadata(db, user.organisation_id, body.drive_file_id)
    if metadata is None:
        raise HTTPException(status_code=400, detail="Google Drive er ikke forbundet")

    candidate = SimpleNamespace(
        file_id=body.drive_file_id,
        name=metadata.get("name", ""),
        mime_type=metadata.get("mimeType", ""),
        modified_time=metadata.get("modifiedTime"),
    )

    try:
        confirmation, lines = await _load_order_confirmation_lines(
            db, user.organisation_id, candidate
        )
    except Exception as e:
        logger.error(f"Could not parse order confirmation {body.drive_file_id}: {e}")
        raise HTTPException(status_code=422, detail=f"Kunne ikke læse filen: {e}")

    if not lines:
        raise HTTPException(
            status_code=422,
            detail="Ingen produktlinjer fundet i ordrebekræftelsen",
        )

    products_result = await db.execute(
        select(ImportProduct).where(
            ImportProduct.import_id == imp.id,
            ImportProduct.order_confirmation_line_id.is_(None),
            ImportProduct.status != "pushed",
        )
    )
    products = list(products_result.scalars().all())
    if not products:
        return {
            "file_name": candidate.name,
            "lines_parsed": len(lines),
            "matched": 0,
            "unmatched": 0,
            "detail": "Alle produkter er allerede matchet",
        }

    matches = match_products_to_order_lines(
        products,
        lines,
        vendor=(confirmation.vendor if confirmation else None),
        season=(confirmation.season if confirmation else None),
    )
    grouped_lines = group_lines_by_match(matches, lines)
    by_id = {p.id: p for p in products}

    for match in matches:
        product = by_id.get(match.product_id)
        if product is None:
            continue
        merge_with_order_data(
            product, grouped_lines.get(match.product_id, []), match=match
        )

    await db.commit()

    logger.info(
        "Linked order confirmation %s to import %s — %d/%d matched",
        candidate.name, import_id, len(matches), len(products),
    )
    return {
        "file_name": candidate.name,
        "lines_parsed": len(lines),
        "matched": len(matches),
        "unmatched": len(products) - len(matches),
    }
