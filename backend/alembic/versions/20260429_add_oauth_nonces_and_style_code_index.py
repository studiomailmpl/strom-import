"""Add oauth_nonces table and style_code index for production hardening.

- oauth_nonces: persists OAuth state tokens in DB (survives redeployments)
- ix_import_products_style_code: speeds up cross-import duplicate detection

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. OAuth nonces table
    op.create_table(
        "oauth_nonces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nonce", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("shop_domain", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("org_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. Index on style_code for duplicate detection performance
    op.create_index(
        "ix_import_products_style_code",
        "import_products",
        ["style_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_products_style_code", table_name="import_products")
    op.drop_table("oauth_nonces")
