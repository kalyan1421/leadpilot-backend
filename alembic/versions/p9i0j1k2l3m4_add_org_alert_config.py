"""add_org_alert_config

Revision ID: p9i0j1k2l3m4
Revises: o8h9i0j1k2l3
Create Date: 2026-07-12 00:00:00.000000

Adds organizations.alert_config — a nullable JSON blob holding the founder's
configurable alert thresholds (wastage_days, zombie_days, performance_gap,
quality_floor), set from the settings page's Alert Configuration section and
read by the insights/leakage engine in app/api/dashboard.py.

Nullable with no server default: existing orgs keep NULL and fall back to the
engine's built-in defaults (3 / 7 / 15 / 40) until they save their own, so no
org's alerting behaviour changes on deploy.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "p9i0j1k2l3m4"
down_revision = "o8h9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("alert_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "alert_config")
