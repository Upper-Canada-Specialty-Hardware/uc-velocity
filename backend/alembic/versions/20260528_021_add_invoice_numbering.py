"""Add invoice_sequence and quote_version to invoices

Enables structured invoice numbers of the form
    {invoice_sequence}-{quote_number}
where quote_number is {UCA}-{quote_sequence:04d}-{quote_version} captured at
invoice-creation time (the quote version is snapshotted when the invoice is
created). invoice_sequence is a per-quote running number (1, 2, 3...).

Existing rows are backfilled:
  - invoice_sequence via ROW_NUMBER() per quote ordered by creation
  - quote_version from the linked "invoice" snapshot (quote_snapshots.invoice_id),
    falling back to 0 when no snapshot is found.

Note: revision id kept short — alembic_version.version_num is VARCHAR(32).

Revision ID: 021_invoice_numbering
Revises: 020_hardware_schedule_ver
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa


revision = '021_invoice_numbering'
down_revision = '020_hardware_schedule_ver'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Add columns (nullable first so existing rows can be backfilled)
    conn.execute(sa.text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS invoice_sequence INTEGER"))
    conn.execute(sa.text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS quote_version INTEGER"))

    # 2. Backfill invoice_sequence: per-quote running number ordered by creation
    conn.execute(sa.text("""
        UPDATE invoices SET invoice_sequence = sub.seq
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY quote_id ORDER BY created_at ASC, id ASC) AS seq
            FROM invoices
        ) AS sub
        WHERE invoices.id = sub.id
    """))

    # 3. Backfill quote_version from the linked invoice snapshot; default 0
    conn.execute(sa.text("""
        UPDATE invoices SET quote_version = COALESCE(
            (SELECT qs.version FROM quote_snapshots qs
             WHERE qs.invoice_id = invoices.id
             ORDER BY qs.version DESC LIMIT 1),
            0
        )
    """))

    # 4. Defaults (safety net for future inserts) + enforce NOT NULL
    conn.execute(sa.text("UPDATE invoices SET invoice_sequence = 1 WHERE invoice_sequence IS NULL"))
    conn.execute(sa.text("UPDATE invoices SET quote_version = 0 WHERE quote_version IS NULL"))
    conn.execute(sa.text("ALTER TABLE invoices ALTER COLUMN invoice_sequence SET DEFAULT 1"))
    conn.execute(sa.text("ALTER TABLE invoices ALTER COLUMN quote_version SET DEFAULT 0"))
    conn.execute(sa.text("ALTER TABLE invoices ALTER COLUMN invoice_sequence SET NOT NULL"))
    conn.execute(sa.text("ALTER TABLE invoices ALTER COLUMN quote_version SET NOT NULL"))


def downgrade():
    op.drop_column('invoices', 'quote_version')
    op.drop_column('invoices', 'invoice_sequence')
