"""Add image_cache table for caching verified product images.

Stores verified images per vendor+style_code so re-imports skip
scraping and Vision verification. Cache entries expire after 30 days
(enforced in application code).

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vendor_lower", sa.String(255), nullable=False),
        sa.Column("style_code_lower", sa.String(255), nullable=False),
        sa.Column("image_urls", JSONB, nullable=False),
        sa.Column("image_source", sa.String(100), server_default=""),
        sa.Column("product_page_url", sa.String(2048), server_default=""),
        sa.Column("details_json", JSONB, server_default="{}"),
        sa.Column("hit_count", sa.Integer, server_default="1"),
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
    )
    op.create_index(
        "ix_image_cache_lookup",
        "image_cache",
        ["org_id", "vendor_lower", "style_code_lower"],
    )


def downgrade() -> None:
    op.drop_index("ix_image_cache_lookup", table_name="image_cache")
    op.drop_table("image_cache")
