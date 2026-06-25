"""Add markup field to brands table.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column("markup", sa.Float(), nullable=False, server_default="2.5"),
    )


def downgrade() -> None:
    op.drop_column("brands", "markup")
