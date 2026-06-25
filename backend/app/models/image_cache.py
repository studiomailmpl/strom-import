"""
ImageCache — caches verified product images by vendor + style_code.

Avoids re-scraping and re-verifying images when the same product
(same vendor + SKU) is imported again within 30 days.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ImageCache(Base):
    __tablename__ = "image_cache"
    __table_args__ = (
        Index("ix_image_cache_lookup", "org_id", "vendor_lower", "style_code_lower"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    vendor_lower: Mapped[str] = mapped_column(String(255), nullable=False)  # lowercased vendor
    style_code_lower: Mapped[str] = mapped_column(String(255), nullable=False)  # lowercased SKU
    image_urls: Mapped[list | None] = mapped_column(JSONB, nullable=False)  # list of verified image URLs
    image_source: Mapped[str] = mapped_column(String(100), default="")  # which strategy found them
    product_page_url: Mapped[str] = mapped_column(String(2048), default="")
    details_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)  # {material, description_en}
    hit_count: Mapped[int] = mapped_column(Integer, default=1)  # how many times this cache entry was used
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
