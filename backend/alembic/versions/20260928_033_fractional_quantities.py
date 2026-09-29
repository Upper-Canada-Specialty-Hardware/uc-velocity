"""Store quote and invoice quantities as floats so labour and misc can be fractional.

Upgrade: turns the 14 quantity columns on quote lines, quote line history
(snapshots), invoice lines and invoice line history from integer into
double precision (Postgres floating point). Every existing whole number is
converted exactly (2 becomes 2.0), so no customer value changes. Parts are
still kept whole by the application; purchase orders and receiving are not
touched and stay integer.

Downgrade: converting back to integer would silently round any fractional
quantity (1.5 hours would become 2), which would change amounts already
quoted or billed. So the downgrade first counts rows holding a fractional
value in any of the 14 columns. If any exist it raises an error and changes
nothing; the fix is to move forward instead. If every value is whole, the
columns are converted back to integer, which is then lossless.

Revision ID: 033_fractional_qty
Revises: 032_clerk_email_receipts
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "033_fractional_qty"
down_revision = "032_clerk_email_receipts"
branch_labels = None
depends_on = None


# Every quantity column that becomes fractional, grouped by table. The live
# tables and their history (snapshot) twins must always share a type.
QUANTITY_COLUMNS = {
    "quote_line_items": ["quantity", "qty_pending", "qty_fulfilled"],
    "quote_line_item_snapshots": ["quantity", "qty_pending", "qty_fulfilled"],
    "invoice_line_items": [
        "qty_ordered",
        "qty_fulfilled_this_invoice",
        "qty_fulfilled_total",
        "qty_pending_after",
    ],
    "invoice_line_item_snapshots": [
        "qty_ordered",
        "qty_fulfilled_this_invoice",
        "qty_fulfilled_total",
        "qty_pending_after",
    ],
}

# Plain-language names for the downgrade error message.
TABLE_LABELS = {
    "quote_line_items": "quote lines",
    "quote_line_item_snapshots": "quote line history rows",
    "invoice_line_items": "invoice lines",
    "invoice_line_item_snapshots": "invoice line history rows",
}


def count_fractional_rows(connection) -> dict:
    """Count rows per table that hold a fractional value in any quantity column.

    Used by ``downgrade()`` to refuse a rollback that would round customer
    quantities; kept as a separate function so tests can call it directly.

    Args:
        connection: An open SQLAlchemy connection to the database.

    Returns:
        A dict mapping table name to the number of rows with at least one
        non-whole quantity (tables with none map to 0).
    """
    counts = {}
    for table, columns in QUANTITY_COLUMNS.items():
        # A value is fractional when it differs from its own whole-number floor
        condition = " OR ".join(f"({col} IS NOT NULL AND {col} <> floor({col}))" for col in columns)
        counts[table] = connection.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {condition}")  # names come from the constant above
        ).scalar_one()
    return counts


def assert_no_fractional_quantities(connection) -> None:
    """Raise if any quantity is fractional, so a downgrade never alters billed amounts.

    Args:
        connection: An open SQLAlchemy connection to the database.

    Raises:
        RuntimeError: When at least one row holds a fractional quantity; the
            message lists how many rows per table.
    """
    counts = count_fractional_rows(connection)
    found = {table: n for table, n in counts.items() if n}  # only tables with offenders
    if found:
        detail = ", ".join(f"{n} {TABLE_LABELS[table]}" for table, n in found.items())
        raise RuntimeError(
            f"Refusing to downgrade: {detail} hold fractional quantities; "
            "downgrading would change billed amounts. Fix forward instead."
        )


def upgrade() -> None:
    """Convert every quote/invoice quantity column from integer to double precision."""
    for table, columns in QUANTITY_COLUMNS.items():
        for col in columns:
            # Integer -> double precision is exact; nullability and (Python-side) defaults are unchanged
            op.alter_column(
                table,
                col,
                existing_type=sa.Integer(),
                type_=sa.Float(),
                postgresql_using=f"{col}::double precision",
            )


def downgrade() -> None:
    """Convert back to integer only when every stored quantity is already whole."""
    # Refuse before touching anything: rounding 1.5 to 2 would rewrite customer data
    assert_no_fractional_quantities(op.get_bind())
    for table, columns in QUANTITY_COLUMNS.items():
        for col in columns:
            # Values are all whole here, so the integer cast loses nothing
            op.alter_column(
                table,
                col,
                existing_type=sa.Float(),
                type_=sa.Integer(),
                postgresql_using=f"{col}::integer",
            )
