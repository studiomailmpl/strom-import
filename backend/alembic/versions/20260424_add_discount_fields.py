"""Add discount_pct and gross_price_eur to import_products.

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_products", sa.Column("discount_pct", sa.Float(), nullable=True, server_default="0"))
    op.add_column("import_products", sa.Column("gross_price_eur", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_products", "gross_price_eur")
    op.drop_column("import_products", "discount_pct")
