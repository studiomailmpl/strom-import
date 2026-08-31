"""add drive_folder_id to brands

Revision ID: e5a71c93b06d
Revises: d3f5a9c14e28
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "e5a71c93b06d"
down_revision = "d3f5a9c14e28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column(
            "drive_folder_id",
            sa.String(length=255),
            nullable=True,
            comment=(
                "Drive folder ID with this brand's product images. The last "
                "path segment of the folder's URL in Drive."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "drive_folder_id")
