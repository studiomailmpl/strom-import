"""Add keyword_performance and search_console_configs tables for SEO Layer 2.

keyword_performance: stores aggregated Search Console search data per product type.
search_console_configs: per-tenant Google Search Console OAuth config.

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # keyword_performance table
    op.create_table(
        "keyword_performance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_type", sa.String(255), nullable=False),
        sa.Column("material_hint", sa.String(255), server_default=""),
        sa.Column("keyword", sa.String(512), nullable=False),
        sa.Column("clicks", sa.Integer, server_default="0"),
        sa.Column("impressions", sa.Integer, server_default="0"),
        sa.Column("avg_position", sa.Float, server_default="0"),
        sa.Column("ctr", sa.Float, server_default="0"),
        sa.Column("landing_page", sa.String(1024), server_default=""),
        sa.Column("sync_period_days", sa.Integer, server_default="28"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kp_org_type", "keyword_performance", ["organisation_id", "product_type"])
    op.create_index("ix_kp_org_clicks", "keyword_performance", ["organisation_id", "clicks"])
    op.create_index("ix_keyword_performance_org_id", "keyword_performance", ["organisation_id"])

    # search_console_configs table
    op.create_table(
        "search_console_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", UUID(as_uuid=True), sa.ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("property_url", sa.String(512), server_default=""),
        sa.Column("access_token_encrypted", sa.String(2048), server_default=""),
        sa.Column("refresh_token_encrypted", sa.String(2048), server_default=""),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="false"),
        sa.Column("sync_period_days", sa.Integer, server_default="28"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("search_console_configs")
    op.drop_index("ix_keyword_performance_org_id", table_name="keyword_performance")
    op.drop_index("ix_kp_org_clicks", table_name="keyword_performance")
    op.drop_index("ix_kp_org_type", table_name="keyword_performance")
    op.drop_table("keyword_performance")
