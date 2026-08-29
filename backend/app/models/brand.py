"""
Brand model — represents a fashion brand/vendor that an organisation works with.
Stores image bank configuration for future portal integrations.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (
        # One brand per org per slug — created in the original brands migration.
        UniqueConstraint("organisation_id", "slug", name="uq_brands_org_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Image bank configuration
    image_bank_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_bank_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # datadwell, canto, trendmark, brandos, custom
    image_bank_search_pattern: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="URL pattern for image bank search. Use {sku} placeholder. "
                "E.g. https://brand.brandos.com/search?q={sku}",
    )
    image_bank_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pricing
    markup: Mapped[float] = mapped_column(
        Float, default=2.5, server_default="2.5",
        comment="Markup multiplier: retail_price = cost_price * markup",
    )

    # Brand website & image scraping
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_url_pattern: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="URL pattern for product search on brand site. Use {sku} placeholder. E.g. https://www.66north.com/search?q={sku}",
    )
    # AI extraction few-shot examples (auto-populated from successful imports)
    extraction_examples: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=None,
        comment="Few-shot extraction examples for AI prompt. Auto-saved from successful imports.",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    organisation: Mapped["Organisation"] = relationship(back_populates="brands")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Brand {self.name} (org={self.organisation_id})>"
