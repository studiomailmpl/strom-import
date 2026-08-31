"""
OrderConfirmation — a parsed order confirmation from the organisation's Drive.

Order confirmations carry the RRP (recommended retail price), which supplier
invoices never do, so they are the only source for it in the pipeline.

Parsing is cached on (drive_file_id, drive_modified_time): if Drive reports the
same modified time as the stored one, the parse is reused instead of spending
another Claude Vision call. Same idea as image_cache.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OrderConfirmation(Base):
    __tablename__ = "order_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Drive's own file id. Unique: one parse per file.
    drive_file_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Drive's modifiedTime, stored as the RFC 3339 string Drive returned. Kept
    # verbatim rather than as a timestamp so the cache comparison is an exact
    # string equality and cannot drift on a parse/format round trip.
    drive_modified_time: Mapped[str | None] = mapped_column(String(64))

    file_name: Mapped[str] = mapped_column(String(512), default="")

    # Header fields read off the confirmation
    vendor: Mapped[str | None] = mapped_column(String(255))
    season: Mapped[str | None] = mapped_column(String(100))
    order_number: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str | None] = mapped_column(String(10))

    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    line_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    lines: Mapped[list["OrderConfirmationLine"]] = relationship(
        back_populates="order_confirmation",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<OrderConfirmation {self.file_name} lines={self.line_count}>"


class OrderConfirmationLine(Base):
    __tablename__ = "order_confirmation_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_confirmations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    style_number: Mapped[str | None] = mapped_column(String(255), index=True)
    sku: Mapped[str | None] = mapped_column(String(255), index=True)
    ean: Mapped[str | None] = mapped_column(String(64))
    product_name: Mapped[str | None] = mapped_column(String(512))
    color_code: Mapped[str | None] = mapped_column(String(100))
    color_name: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[int | None] = mapped_column(Integer)
    wholesale_price: Mapped[float | None] = mapped_column(Float)
    # The reason this table exists — invoices never carry a retail price.
    rrp: Mapped[float | None] = mapped_column(Float)

    # Relationships
    order_confirmation: Mapped["OrderConfirmation"] = relationship(back_populates="lines")

    def __repr__(self) -> str:
        return f"<OrderConfirmationLine {self.style_number} {self.size} rrp={self.rrp}>"
