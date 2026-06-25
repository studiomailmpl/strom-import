"""Add duplicate detection fields to import_products.

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_products",
        sa.Column("duplicate_of_import_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "import_products",
        sa.Column("duplicate_import_date", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_products", "duplicate_import_date")
    op.drop_column("import_products", "duplicate_of_import_id")
