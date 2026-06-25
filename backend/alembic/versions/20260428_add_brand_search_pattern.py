"""Add search_url_pattern to brands for brand-specific image scraping.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column(
            "search_url_pattern",
            sa.Text(),
            nullable=True,
            comment="URL pattern for product search on brand site. Use {sku} placeholder.",
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "search_url_pattern")
