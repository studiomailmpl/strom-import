"""
Import model — one per uploaded PDF invoice batch.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Import(Base):
    __tablename__ = "imports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id"), nullable=False, index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # Human-readable name for this import
    name: Mapped[str] = mapped_column(String(512), default="")

    # Test mode: skip restock detection, add _test-import tag
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)

    # Status: uploading → analysing → review → pushing → completed / failed
    status: Mapped[str] = mapped_column(String(50), default="uploading")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)

    # Pricing settings used for this import
    eur_rate: Mapped[float] = mapped_column(Float, default=7.46)
    markup: Mapped[float] = mapped_column(Float, default=2.5)

    # Summary stats
    total_products: Mapped[int] = mapped_column(Integer, default=0)
    products_pushed: Mapped[int] = mapped_column(Integer, default=0)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    organisation: Mapped["Organisation"] = relationship(back_populates="imports")  # noqa: F821
    created_by: Mapped["User"] = relationship()  # noqa: F821
    products: Mapped[list["ImportProduct"]] = relationship(  # noqa: F821
        back_populates="import_record", cascade="all, delete-orphan"
    )
    files: Mapped[list["ImportFile"]] = relationship(  # noqa: F821
        back_populates="import_record", cascade="all, delete-orphan"
    )
