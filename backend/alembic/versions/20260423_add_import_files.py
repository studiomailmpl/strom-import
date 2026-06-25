"""Add import_files table and name column to imports.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add name column to imports
    op.add_column(
        "imports",
        sa.Column("name", sa.String(512), server_default="", nullable=False),
    )

    # Create import_files table
    op.create_table(
        "import_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "import_id",
            UUID(as_uuid=True),
            sa.ForeignKey("imports.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), server_default="uploading", nullable=False),
        sa.Column("products_found", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("import_files")
    op.drop_column("imports", "name")
