"""
Organisation settings — default EUR rate and markup.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.organisation import Organisation

logger = logging.getLogger(__name__)
router = APIRouter()


class SettingsResponse(BaseModel):
    default_eur_rate: float
    default_markup: float


class SettingsUpdate(BaseModel):
    default_eur_rate: float | None = Field(None, gt=0, le=20)
    default_markup: float | None = Field(None, gt=0, le=10)


@router.get("/settings", response_model=SettingsResponse)
async def get_org_settings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get organisation default settings."""
    result = await db.execute(
        select(Organisation).where(Organisation.id == user.organisation_id)
    )
    org = result.scalar_one_or_none()

    return SettingsResponse(
        default_eur_rate=getattr(org, 'default_eur_rate', 7.46) if org else 7.46,
        default_markup=getattr(org, 'default_markup', 2.5) if org else 2.5,
    )


@router.patch("/settings", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update organisation default settings."""
    result = await db.execute(
        select(Organisation).where(Organisation.id == user.organisation_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    if data.default_eur_rate is not None:
        org.default_eur_rate = data.default_eur_rate
    if data.default_markup is not None:
        org.default_markup = data.default_markup

    await db.flush()
    await db.refresh(org)

    return SettingsResponse(
        default_eur_rate=org.default_eur_rate,
        default_markup=org.default_markup,
    )
