"""Add DataForSEO encrypted credentials to search_console_configs.

Per-tenant DataForSEO API credentials, stored encrypted.
Enables users to configure DataForSEO from the frontend settings page.

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_console_configs",
        sa.Column("dataforseo_login_encrypted", sa.String(1024), server_default="", nullable=False),
    )
    op.add_column(
        "search_console_configs",
        sa.Column("dataforseo_password_encrypted", sa.String(1024), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("search_console_configs", "dataforseo_password_encrypted")
    op.drop_column("search_console_configs", "dataforseo_login_encrypted")
