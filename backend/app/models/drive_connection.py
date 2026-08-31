"""
DriveConnection — one per organisation. Stores encrypted Google Drive OAuth
tokens so order confirmations can be read from the customer's own Drive.

Mirrors the Search Console connection in keyword_performance.SearchConsoleConfig:
tokens are Fernet-encrypted via app.core.security before they touch the database.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DriveConnection(Base):
    __tablename__ = "drive_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one Drive connection per organisation
    )

    # Fernet-encrypted OAuth tokens — never store these in plaintext.
    encrypted_access_token: Mapped[str] = mapped_column(String(2048), default="")
    encrypted_refresh_token: Mapped[str] = mapped_column(String(2048), default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optional: restrict searches to a single Drive folder. Empty means "all of Drive".
    root_folder_id: Mapped[str | None] = mapped_column(String(255))

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The user who authorised the connection. Nullable: the OAuth callback
    # resolves this from the stored nonce, which may not carry a usable id.
    connected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # Relationships
    organisation: Mapped["Organisation"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<DriveConnection org={self.organisation_id}>"
