"""
Persistence and caching for parsed order confirmations.

Parsing a PDF confirmation costs a Claude Vision call, so a parse is reused
whenever Drive reports the same modifiedTime as the stored one — the same
"skip the expensive work if nothing changed" idea as image_cache.
"""

import logging
import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.order_confirmation import OrderConfirmation, OrderConfirmationLine
from app.services.order_confirmation_parser import (
    ParsedOrderConfirmation,
    ParsedOrderConfirmationLine,
)

logger = logging.getLogger(__name__)


async def get_cached_confirmation(
    db: AsyncSession,
    org_id: uuid.UUID,
    drive_file_id: str,
    drive_modified_time: str | None,
) -> OrderConfirmation | None:
    """
    Return the stored parse for this Drive file if it is still current.

    Returns None when the file was never parsed, or when Drive reports a
    different modifiedTime — meaning the file changed and must be re-parsed.
    """
    result = await db.execute(
        select(OrderConfirmation)
        .options(selectinload(OrderConfirmation.lines))
        .where(
            OrderConfirmation.organisation_id == org_id,
            OrderConfirmation.drive_file_id == drive_file_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return None

    if drive_modified_time and existing.drive_modified_time != drive_modified_time:
        logger.info(
            "Order confirmation %s changed in Drive (%s → %s) — re-parsing",
            drive_file_id, existing.drive_modified_time, drive_modified_time,
        )
        return None

    return existing


async def save_confirmation(
    db: AsyncSession,
    org_id: uuid.UUID,
    drive_file_id: str,
    drive_modified_time: str | None,
    file_name: str,
    parsed: ParsedOrderConfirmation,
) -> tuple[OrderConfirmation, list[OrderConfirmationLine]]:
    """
    Store a parse, replacing any previous one for the same Drive file.

    The row is reused rather than recreated so its id stays stable for anything
    already referencing it; the lines are replaced wholesale.

    Returns the confirmation *and* the line rows. Callers must use the returned
    list rather than reading `confirmation.lines`: the relationship was never
    populated here, so touching it emits a lazy SELECT, which raises
    MissingGreenlet on an AsyncSession.
    """
    result = await db.execute(
        select(OrderConfirmation).where(
            OrderConfirmation.organisation_id == org_id,
            OrderConfirmation.drive_file_id == drive_file_id,
        )
    )
    confirmation = result.scalar_one_or_none()

    if confirmation is None:
        confirmation = OrderConfirmation(
            organisation_id=org_id,
            drive_file_id=drive_file_id,
        )
        db.add(confirmation)
        await db.flush()
    else:
        # Drop the old lines before writing the new ones.
        await db.execute(
            sa_delete(OrderConfirmationLine).where(
                OrderConfirmationLine.order_confirmation_id == confirmation.id
            )
        )

    confirmation.drive_modified_time = drive_modified_time
    confirmation.file_name = file_name
    confirmation.vendor = parsed.vendor or None
    confirmation.season = parsed.season or None
    confirmation.order_number = parsed.order_number or None
    confirmation.currency = parsed.currency or None
    confirmation.line_count = len(parsed.lines)

    line_models = [_to_model(confirmation.id, line) for line in parsed.lines]
    for model in line_models:
        db.add(model)

    await db.flush()
    return confirmation, line_models


def _to_model(
    confirmation_id: uuid.UUID, line: ParsedOrderConfirmationLine
) -> OrderConfirmationLine:
    return OrderConfirmationLine(
        order_confirmation_id=confirmation_id,
        style_number=line.style_number or None,
        sku=line.sku or None,
        ean=line.ean or None,
        product_name=line.product_name or None,
        color_code=line.color_code or None,
        color_name=line.color_name or None,
        size=line.size or None,
        quantity=line.quantity,
        wholesale_price=line.wholesale_price,
        rrp=line.rrp,
    )


def serialise_confirmation(
    confirmation: OrderConfirmation,
    include_lines: bool = True,
    lines: list[OrderConfirmationLine] | None = None,
) -> dict:
    """
    Shape an OrderConfirmation for an API response.

    Pass `lines` explicitly for a confirmation that was just saved — its
    relationship is unloaded, and reading it would lazy-load under async.
    Omit it only when the confirmation came from get_cached_confirmation, which
    eager-loads the collection.
    """
    payload = {
        "id": str(confirmation.id),
        "drive_file_id": confirmation.drive_file_id,
        "drive_modified_time": confirmation.drive_modified_time,
        "file_name": confirmation.file_name,
        "vendor": confirmation.vendor,
        "season": confirmation.season,
        "order_number": confirmation.order_number,
        "currency": confirmation.currency,
        "line_count": confirmation.line_count,
        "parsed_at": confirmation.parsed_at.isoformat() if confirmation.parsed_at else None,
    }
    if include_lines:
        line_rows = lines if lines is not None else confirmation.lines
        payload["lines"] = [
            {
                "style_number": line.style_number,
                "sku": line.sku,
                "ean": line.ean,
                "product_name": line.product_name,
                "color_code": line.color_code,
                "color_name": line.color_name,
                "size": line.size,
                "quantity": line.quantity,
                "wholesale_price": line.wholesale_price,
                "rrp": line.rrp,
            }
            for line in line_rows
        ]
    return payload
