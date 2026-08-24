"""Category legacy key -> composite (legacy_source, legacy_id, type).

A single Vision category typed "Application & Material" migrates to BOTH a part and
a labour category that share one CategoryID. Under the original
``(legacy_source, legacy_id)`` partial-unique index those two rows collide, so the
key must include ``type``. This alters ``uq_categories_legacy`` accordingly (still
partial: only where ``legacy_id IS NOT NULL``, so user-created categories are
unconstrained).

Note: revision id kept short (alembic_version.version_num is VARCHAR(32)). The chain
position here (down_revision) is provisional -- the migration stack is linearized at
package-assembly; this only needs to be internally consistent with a working downgrade.

Revision ID: 029_category_composite
Revises: 028_legacy_keys
Create Date: 2026-08-17
"""
from alembic import op


revision = '029_category_composite'
down_revision = '028_legacy_keys'
branch_labels = None
depends_on = None

_INDEX = "uq_categories_legacy"
_WHERE = "legacy_id IS NOT NULL"


def upgrade():
    # Swap the partial-unique index to INCLUDE type, so a part-category and a
    # labour-category sharing one Vision CategoryID no longer collide. Raw SQL because
    # the index is PARTIAL (WHERE legacy_id IS NOT NULL) -- explicit and unambiguous.
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON categories "
        f"(legacy_source, legacy_id, type) WHERE {_WHERE}"
    )


def downgrade():
    # Restore the original two-column partial-unique index.
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON categories "
        f"(legacy_source, legacy_id) WHERE {_WHERE}"
    )
