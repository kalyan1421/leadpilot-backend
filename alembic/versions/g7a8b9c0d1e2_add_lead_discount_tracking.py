"""add_lead_discount_tracking

Revision ID: g7a8b9c0d1e2
Revises: c9d0e1f2a3b4
Create Date: 2026-07-07 00:00:00.000000

Adds leads.discount_pct / leads.list_price (PRD Layer 4-C — Discount & Deal
Margin Tracker). Both nullable: only populated going forward when a telecaller
records them at Closed Won (deal_value already exists, see
e5f6a7b8c9d0_add_revenue_tracking). Margin = list_price - deal_value.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "g7a8b9c0d1e2"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("discount_pct", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("list_price", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "list_price")
    op.drop_column("leads", "discount_pct")
