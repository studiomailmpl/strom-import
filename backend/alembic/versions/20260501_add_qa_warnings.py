"""Add qa_warnings column to import_products.

Stores QA validation results as JSONB array of warning objects.
Each warning has: level, code, field, message.

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_products",
        sa.Column("qa_warnings", JSONB, server_default="[]", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_products", "qa_warnings")
