"""
KeywordPerformance — aggregated search keyword data from Google Search Console.

Stores top-performing keywords per product type and material combination,
enabling the AI to generate better keywords over time (Layer 2 feedback loop).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KeywordPerformance(Base):
    __tablename__ = "keyword_performance"
    __table_args__ = (
        # Query pattern: "get top keywords for product_type X in organisation Y"
        Index("ix_kp_org_type", "organisation_id", "product_type"),
        # Query pattern: "get all keywords for this org sorted by clicks"
        Index("ix_kp_org_clicks", "organisation_id", "clicks"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # What product category does this keyword serve?
    product_type: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional: material qualifier (e.g., "uld" for wool products)
    material_hint: Mapped[str] = mapped_column(String(255), default="")

    # The actual search keyword
    keyword: Mapped[str] = mapped_column(String(512), nullable=False)

    # Search Console metrics (aggregated over sync_period_days)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    avg_position: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)  # Click-through rate

    # The landing page URL that received these clicks (for mapping back to product type)
    landing_page: Mapped[str] = mapped_column(String(1024), default="")

    # Period this data covers
    sync_period_days: Mapped[int] = mapped_column(Integer, default=28)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchConsoleConfig(Base):
    """
    Per-tenant Google Search Console configuration.

    Stores OAuth tokens and property URL for the tenant's website,
    enabling periodic keyword performance sync.
    """
    __tablename__ = "search_console_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )

    # Google Search Console property URL (e.g., "sc-domain:stromstore.dk")
    property_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # Google OAuth2 tokens (encrypted)
    access_token_encrypted: Mapped[str] = mapped_column(String(2048), default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(String(2048), default="")
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Sync settings
    is_active: Mapped[bool] = mapped_column(default=False)
    sync_period_days: Mapped[int] = mapped_column(Integer, default=28)

    # DataForSEO credentials (encrypted, per-tenant)
    dataforseo_login_encrypted: Mapped[str] = mapped_column(String(1024), default="")
    dataforseo_password_encrypted: Mapped[str] = mapped_column(String(1024), default="")

    # Last successful sync
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
