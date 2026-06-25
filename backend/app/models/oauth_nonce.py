"""
OAuthNonce — persisted OAuth state tokens for Shopify install flow.

Replaces the in-memory dict to survive redeployments. Nonces auto-expire
after 10 minutes and are cleaned up on each insert.
"""

import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Nonce validity window
NONCE_TTL = timedelta(minutes=10)


class OAuthNonce(Base):
    __tablename__ = "oauth_nonces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def is_expired(self) -> bool:
        if not self.created_at:
            return True
        now = datetime.now(timezone.utc)
        created = self.created_at.replace(tzinfo=timezone.utc) if self.created_at.tzinfo is None else self.created_at
        return (now - created) > NONCE_TTL
