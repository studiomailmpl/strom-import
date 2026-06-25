"""Add is_test to imports for test mode.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("is_test", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("imports", "is_test")
