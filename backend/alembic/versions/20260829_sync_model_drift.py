"""Sync schema with models: NOT NULL, composite index, index rename, FK cascade, comments.

Closes the drift that `alembic check` reported: several columns are declared as
non-optional `Mapped[...]` in the models but were created nullable, the composite
import_products index was never created, the keyword_performance org index was
created under a non-default name, and the shopify_connections FK was missing
ON DELETE CASCADE.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa

revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


# Columns the models declare as non-optional Mapped[...] (NOT NULL) but that the
# original migrations created as nullable. All have Python-side or server-side
# defaults, and no existing row holds NULL in any of them.
NOT_NULL_COLUMNS = [
    ("brands", "created_at", sa.DateTime(timezone=True)),
    ("brands", "updated_at", sa.DateTime(timezone=True)),
    ("image_cache", "image_source", sa.String(100)),
    ("image_cache", "product_page_url", sa.String(2048)),
    ("image_cache", "hit_count", sa.Integer()),
    ("image_cache", "created_at", sa.DateTime(timezone=True)),
    ("image_cache", "updated_at", sa.DateTime(timezone=True)),
    ("keyword_performance", "material_hint", sa.String(255)),
    ("keyword_performance", "clicks", sa.Integer()),
    ("keyword_performance", "impressions", sa.Integer()),
    ("keyword_performance", "avg_position", sa.Float()),
    ("keyword_performance", "ctr", sa.Float()),
    ("keyword_performance", "landing_page", sa.String(1024)),
    ("keyword_performance", "sync_period_days", sa.Integer()),
    ("keyword_performance", "created_at", sa.DateTime(timezone=True)),
    ("keyword_performance", "updated_at", sa.DateTime(timezone=True)),
    ("oauth_nonces", "created_at", sa.DateTime(timezone=True)),
    ("product_images", "created_at", sa.DateTime(timezone=True)),
    ("search_console_configs", "property_url", sa.String(512)),
    ("search_console_configs", "access_token_encrypted", sa.String(2048)),
    ("search_console_configs", "refresh_token_encrypted", sa.String(2048)),
    ("search_console_configs", "is_active", sa.Boolean()),
    ("search_console_configs", "sync_period_days", sa.Integer()),
    ("search_console_configs", "created_at", sa.DateTime(timezone=True)),
    ("search_console_configs", "updated_at", sa.DateTime(timezone=True)),
]

MARKUP_COMMENT = "Markup multiplier: retail_price = cost_price * markup"
SEARCH_PATTERN_COMMENT_OLD = (
    "URL pattern for product search on brand site. Use {sku} placeholder."
)
SEARCH_PATTERN_COMMENT_NEW = (
    "URL pattern for product search on brand site. Use {sku} placeholder. "
    "E.g. https://www.66north.com/search?q={sku}"
)


def upgrade() -> None:
    for table, column, type_ in NOT_NULL_COLUMNS:
        op.alter_column(table, column, existing_type=type_, nullable=False)

    # Composite index for "all products for import X with status Y".
    op.create_index(
        "ix_import_products_import_status",
        "import_products",
        ["import_id", "status"],
    )

    # The model uses index=True, which yields the default index name.
    op.execute(
        "ALTER INDEX ix_keyword_performance_org_id "
        "RENAME TO ix_keyword_performance_organisation_id"
    )

    # Model declares ondelete="CASCADE"; the DB constraint was NO ACTION.
    op.drop_constraint(
        "shopify_connections_organisation_id_fkey",
        "shopify_connections",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "shopify_connections_organisation_id_fkey",
        "shopify_connections",
        "organisations",
        ["organisation_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.alter_column(
        "brands", "markup",
        existing_type=sa.Float(),
        existing_nullable=False,
        comment=MARKUP_COMMENT,
        existing_comment=None,
    )
    op.alter_column(
        "brands", "search_url_pattern",
        existing_type=sa.Text(),
        existing_nullable=True,
        comment=SEARCH_PATTERN_COMMENT_NEW,
        existing_comment=SEARCH_PATTERN_COMMENT_OLD,
    )


def downgrade() -> None:
    op.alter_column(
        "brands", "search_url_pattern",
        existing_type=sa.Text(),
        existing_nullable=True,
        comment=SEARCH_PATTERN_COMMENT_OLD,
        existing_comment=SEARCH_PATTERN_COMMENT_NEW,
    )
    op.alter_column(
        "brands", "markup",
        existing_type=sa.Float(),
        existing_nullable=False,
        comment=None,
        existing_comment=MARKUP_COMMENT,
    )

    op.drop_constraint(
        "shopify_connections_organisation_id_fkey",
        "shopify_connections",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "shopify_connections_organisation_id_fkey",
        "shopify_connections",
        "organisations",
        ["organisation_id"],
        ["id"],
    )

    op.execute(
        "ALTER INDEX ix_keyword_performance_organisation_id "
        "RENAME TO ix_keyword_performance_org_id"
    )

    op.drop_index("ix_import_products_import_status", table_name="import_products")

    for table, column, type_ in reversed(NOT_NULL_COLUMNS):
        op.alter_column(table, column, existing_type=type_, nullable=True)
