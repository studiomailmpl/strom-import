"""
ProductImage — uploaded image for an import product.
Stored on local filesystem, served via API endpoint.
Takes priority over web-scraped images during Shopify push.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("import_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # File info
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)  # Relative path from image_upload_dir
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)  # Bytes

    # Display order (0 = first/primary)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    product: Mapped["ImportProduct"] = relationship(back_populates="uploaded_images")  # noqa: F821

    def __repr__(self) -> str:
        return f"<ProductImage {self.filename} (product={self.product_id})>"
