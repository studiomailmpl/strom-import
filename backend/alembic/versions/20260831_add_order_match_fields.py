"""add order confirmation match fields to import_products

Hand-written for the same reason as c244e1f8b7a0: the target database is behind
this branch, and alembic will not autogenerate against a database that is not at
head. Verified with alembic.autogenerate.compare_metadata — see the commit.

Revision ID: d3f5a9c14e28
Revises: c244e1f8b7a0
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "d3f5a9c14e28"
down_revision = "c244e1f8b7a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_products",
        sa.Column("order_confirmation_line_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column("import_products", sa.Column("match_confidence", sa.Integer(), nullable=True))
    op.add_column("import_products", sa.Column("match_method", sa.String(length=50), nullable=True))
    op.add_column("import_products", sa.Column("data_sources", JSONB(), nullable=True))

    # SET NULL rather than CASCADE: re-parsing a confirmation replaces its
    # lines, and that must not delete the imported products.
    op.create_foreign_key(
        "import_products_order_confirmation_line_id_fkey",
        "import_products",
        "order_confirmation_lines",
        ["order_confirmation_line_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "import_products_order_confirmation_line_id_fkey",
        "import_products",
        type_="foreignkey",
    )
    op.drop_column("import_products", "data_sources")
    op.drop_column("import_products", "match_method")
    op.drop_column("import_products", "match_confidence")
    op.drop_column("import_products", "order_confirmation_line_id")
