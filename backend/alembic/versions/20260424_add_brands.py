"""Add brands table for brand management and image bank configuration.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organisation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, index=True),
        sa.Column("image_bank_url", sa.Text(), nullable=True),
        sa.Column("image_bank_type", sa.String(50), nullable=True),
        sa.Column("image_bank_notes", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        # Unique constraint: one brand per org per slug
        sa.UniqueConstraint("organisation_id", "slug", name="uq_brands_org_slug"),
    )


def downgrade() -> None:
    op.drop_table("brands")
