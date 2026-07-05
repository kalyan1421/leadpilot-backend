"""add_revenue_tracking

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-05 00:00:00.000000

Adds the columns backing the founder dashboard's Revenue chart, Monthly Goal
gauge, and Live Activity feed — previously all hardcoded on the frontend with
no data model to back them:
- organizations.monthly_revenue_target: founder-set goal, nullable (no target
  set = no fabricated on-target/off-target breakdown)
- leads.deal_value / leads.closed_at: populated when a lead is moved to
  "Closed Won" on the kanban board
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("monthly_revenue_target", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("deal_value", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_leads_closed_at"), "leads", ["closed_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_leads_closed_at"), table_name="leads")
    op.drop_column("leads", "closed_at")
    op.drop_column("leads", "deal_value")
    op.drop_column("organizations", "monthly_revenue_target")
