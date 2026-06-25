"""Add image_source column to import_products.

Tracks which pipeline strategy (brand_website, google, image_bank, cache, etc.)
found the images for each product.

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_products",
        sa.Column("image_source", sa.String(100), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("import_products", "image_source")
