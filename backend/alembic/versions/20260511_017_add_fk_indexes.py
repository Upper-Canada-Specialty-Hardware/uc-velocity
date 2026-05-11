"""Add indexes on FK columns used in list/join queries.

Postgres does not auto-create indexes for foreign key columns. Without these,
join-heavy reads (e.g. fetching the line items for a quote/PO, listing parts
by vendor, listing projects by customer) fall back to sequential scans on the
referenced tables. As table sizes grow this becomes the bottleneck for list
endpoints and detail page loads.

Indexes added:
- quote_line_items.quote_id
- po_line_items.purchase_order_id
- quotes.project_id
- purchase_orders.project_id
- purchase_orders.vendor_id
- projects.customer_id
- parts.vendor_id

All use IF NOT EXISTS guards per project convention.

Revision ID: 017_add_fk_indexes
Revises: 016_add_company_logo
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa


revision = '017_add_fk_indexes'
down_revision = '016_add_company_logo'
branch_labels = None
depends_on = None


# (index_name, table, column) tuples
_INDEXES = [
    ('ix_quote_line_items_quote_id', 'quote_line_items', 'quote_id'),
    ('ix_po_line_items_purchase_order_id', 'po_line_items', 'purchase_order_id'),
    ('ix_quotes_project_id', 'quotes', 'project_id'),
    ('ix_purchase_orders_project_id', 'purchase_orders', 'project_id'),
    ('ix_purchase_orders_vendor_id', 'purchase_orders', 'vendor_id'),
    ('ix_projects_customer_id', 'projects', 'customer_id'),
    ('ix_parts_vendor_id', 'parts', 'vendor_id'),
]


def upgrade() -> None:
    conn = op.get_bind()
    for name, table, column in _INDEXES:
        conn.execute(sa.text(
            f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})'
        ))


def downgrade() -> None:
    conn = op.get_bind()
    for name, _table, _column in _INDEXES:
        conn.execute(sa.text(f'DROP INDEX IF EXISTS {name}'))
