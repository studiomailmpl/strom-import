"""Add missing fields to import_products and organisations.

Revision ID: a1b2c3d4e5f6
Revises: bbb49c46d9e4
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "a1b2c3d4e5f6"
down_revision = "bbb49c46d9e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- import_products: new extraction fields ---
    op.add_column("import_products", sa.Column("material", sa.String(512), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("gender", sa.String(50), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("season", sa.String(100), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("country_of_origin", sa.String(100), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("hs_code", sa.String(20), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("description_en", sa.Text(), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("ai_tags", JSONB(), server_default="[]", nullable=True))
    op.add_column("import_products", sa.Column("color_original", sa.String(255), server_default="", nullable=False))
    op.add_column("import_products", sa.Column("handle", sa.String(512), server_default="", nullable=False))

    # Add index on status for filtered queries
    op.create_index("ix_import_products_status", "import_products", ["status"])

    # --- organisations: default pricing settings ---
    op.add_column("organisations", sa.Column("default_eur_rate", sa.Float(), server_default="7.46", nullable=False))
    op.add_column("organisations", sa.Column("default_markup", sa.Float(), server_default="2.5", nullable=False))


def downgrade() -> None:
    op.drop_column("organisations", "default_markup")
    op.drop_column("organisations", "default_eur_rate")
    op.drop_index("ix_import_products_status", "import_products")
    op.drop_column("import_products", "handle")
    op.drop_column("import_products", "color_original")
    op.drop_column("import_products", "ai_tags")
    op.drop_column("import_products", "description_en")
    op.drop_column("import_products", "hs_code")
    op.drop_column("import_products", "country_of_origin")
    op.drop_column("import_products", "season")
    op.drop_column("import_products", "gender")
    op.drop_column("import_products", "material")
