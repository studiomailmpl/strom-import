"""
Organisation model — one per company/store.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    clerk_org_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )

    default_eur_rate: Mapped[float] = mapped_column(Float, default=7.46)
    default_markup: Mapped[float] = mapped_column(Float, default=2.5)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="organisation")  # noqa: F821
    shopify_connections: Mapped[list["ShopifyConnection"]] = relationship(  # noqa: F821
        back_populates="organisation"
    )
    imports: Mapped[list["Import"]] = relationship(back_populates="organisation")  # noqa: F821
    brands: Mapped[list["Brand"]] = relationship(back_populates="organisation")  # noqa: F821
