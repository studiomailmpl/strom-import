"""Add restock (supplering) fields to import_products.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_products", sa.Column("is_restock", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("import_products", sa.Column("shopify_match_id", sa.String(255), nullable=True))
    op.add_column("import_products", sa.Column("shopify_match_title", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("import_products", "shopify_match_title")
    op.drop_column("import_products", "shopify_match_id")
    op.drop_column("import_products", "is_restock")
