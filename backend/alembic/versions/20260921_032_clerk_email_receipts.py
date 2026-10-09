"""Add durable receipts for Clerk email webhook delivery.

The table stores only Clerk's opaque email id and provider-acceptance time. It
does not retain recipients, subjects, bodies, or one-time codes.

Revision ID: 032_clerk_email_receipts
Revises: 031_staff_roles
Create Date: 2026-09-21
"""
from alembic import op
import sqlalchemy as sa


revision = "032_clerk_email_receipts"
down_revision = "031_staff_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the additive receipt table without changing existing data."""
    op.create_table(
        "clerk_email_deliveries",
        # Clerk's email resource id is the durable idempotency key.
        sa.Column("clerk_email_id", sa.String(length=255), nullable=False),
        # Let PostgreSQL record acceptance time independently of app clocks.
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("clerk_email_id"),
    )


def downgrade() -> None:
    """Remove the receipt table when explicitly rolling back this migration."""
    op.drop_table("clerk_email_deliveries")
