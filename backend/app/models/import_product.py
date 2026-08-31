"""
ImportProduct — one per product extracted from an invoice.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ImportProduct(Base):
    __tablename__ = "import_products"
    __table_args__ = (
        # Composite index for the common query: "get all products for import X with status Y"
        Index("ix_import_products_import_status", "import_id", "status"),
        # Index for cross-import duplicate detection by style_code
        Index("ix_import_products_style_code", "style_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("imports.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Product data from AI extraction
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    vendor: Mapped[str] = mapped_column(String(255), nullable=False)
    product_type: Mapped[str] = mapped_column(String(255), default="")
    description_da: Mapped[str] = mapped_column(Text, default="")
    style_code: Mapped[str] = mapped_column(String(255), default="")
    color: Mapped[str] = mapped_column(String(255), default="")
    color_code: Mapped[str] = mapped_column(String(255), default="")
    material: Mapped[str] = mapped_column(String(512), default="")
    gender: Mapped[str] = mapped_column(String(50), default="")
    season: Mapped[str] = mapped_column(String(100), default="")
    country_of_origin: Mapped[str] = mapped_column(String(100), default="")
    hs_code: Mapped[str] = mapped_column(String(20), default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    ai_tags: Mapped[list | None] = mapped_column(JSONB, default=list)
    seo_keywords: Mapped[list | None] = mapped_column(JSONB, default=list)
    color_original: Mapped[str] = mapped_column(String(255), default="")
    handle: Mapped[str] = mapped_column(String(512), default="")
    image_source: Mapped[str] = mapped_column(String(100), default="")  # which pipeline strategy found images

    # Order / invoice provenance — one invoice PDF can cover several orders,
    # so these live per product rather than only on the Import.
    order_number: Mapped[str | None] = mapped_column(String(100))
    invoice_number: Mapped[str | None] = mapped_column(String(100))

    # Season as written on the invoice, plus the canonical form (AW26, SS27, ...)
    season_raw: Mapped[str | None] = mapped_column(String(100))
    season_normalized: Mapped[str | None] = mapped_column(String(20))

    # Pricing
    cost_price_eur: Mapped[float | None] = mapped_column(Float)
    cost_price_dkk: Mapped[float | None] = mapped_column(Float)
    retail_price_dkk: Mapped[float | None] = mapped_column(Float)
    discount_pct: Mapped[float | None] = mapped_column(Float, default=0)
    gross_price_eur: Mapped[float | None] = mapped_column(Float)  # Pre-discount price

    # Variants as JSON: [{"size": "M", "quantity": 2, "ean": "..."}, ...]
    variants: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Images as JSON: ["https://...", ...]
    images: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Status per product: pending → approved → pushed / skipped / error
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    shopify_product_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)

    # Restock (supplering): matched to existing Shopify product by SKU
    is_restock: Mapped[bool] = mapped_column(Boolean, default=False)
    shopify_match_id: Mapped[str | None] = mapped_column(String(255))  # Shopify GID of matched product
    shopify_match_title: Mapped[str | None] = mapped_column(String(512))  # Title of matched product for display

    # Duplicate warning: set if this SKU was found in a previous import (advisory only, never blocks)
    duplicate_of_import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    duplicate_import_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # QA warnings as JSON: [{"level": "warning", "code": "...", "field": "...", "message": "..."}, ...]
    qa_warnings: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Match against a parsed order confirmation line — the only source of RRP.
    order_confirmation_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_confirmation_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    match_confidence: Mapped[int | None] = mapped_column(Integer)  # 0-100
    match_method: Mapped[str | None] = mapped_column(String(50))

    # Which source won each field, e.g. {"rrp": "order_confirmation",
    # "quantity": "invoice", "images": "web"}. Lets the UI show provenance and
    # makes a wrong merge traceable after the fact.
    data_sources: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    # User can edit before push
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    import_record: Mapped["Import"] = relationship(back_populates="products")  # noqa: F821
    uploaded_images: Mapped[list["ProductImage"]] = relationship(  # noqa: F821
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order",
    )
