"""Add image_bank_search_pattern to brands for DAM portal search integration.

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column(
            "image_bank_search_pattern",
            sa.Text(),
            nullable=True,
            comment="URL pattern for image bank search. Use {sku} placeholder. "
                    "E.g. https://brand.brandos.com/search?q={sku}",
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "image_bank_search_pattern")
