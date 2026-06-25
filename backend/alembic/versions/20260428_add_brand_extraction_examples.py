"""Add extraction_examples JSON field to brands for few-shot AI prompting.

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column(
            "extraction_examples",
            JSON(),
            nullable=True,
            comment="Few-shot extraction examples for AI prompt. Auto-saved from successful imports.",
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "extraction_examples")
