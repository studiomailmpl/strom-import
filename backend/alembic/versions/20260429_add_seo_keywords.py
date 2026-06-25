"""Add seo_keywords JSONB column to import_products.

Stores AI-generated, product-specific SEO search terms (2-3 per product).

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_products",
        sa.Column("seo_keywords", JSONB, server_default="[]", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_products", "seo_keywords")
