"""add_org_logo_and_address

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-07 00:00:00.000000

Adds organizations.logo_url / organizations.address so the telecaller mobile
app's Profile screen can show who a telecaller works for (org name, logo,
address), not just the telecaller's own name. Both nullable — set from the
founder web app's org settings page; no existing org has these yet.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("logo_url", sa.String(length=500), nullable=True))
    op.add_column("organizations", sa.Column("address", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "address")
    op.drop_column("organizations", "logo_url")
