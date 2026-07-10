"""add_org_strict_lead_scoping

Revision ID: m6f7g8h9i0j1
Revises: l5e6f7g8h9i0
Create Date: 2026-07-09 00:00:00.000000

Adds organizations.strict_lead_scoping (LEAD_ASSIGNMENT_SPEC P0-4) — the
per-org rollout flag that gates the new telecaller-scoped inbox/lead-detail
filtering (P0-2). When False (the default), get_inbox/get_lead_detail keep
today's org-wide visibility for every role, including telecaller; when True,
a telecaller only sees leads where Lead.assigned_to == self.id.

Additive, two-phase-safe column: NOT NULL with server_default='false', so
existing rows (every org created before this migration) get a real, concrete
value at the DB level the moment the column is added — no backfill loop
needed, and no window where an old app instance could INSERT a row missing
this column (the server_default covers it regardless of deploy ordering).

Reversible: downgrade drops the column. No data loss beyond the flag itself,
which every org defaults back to False (org-wide visibility) if re-added.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "m6f7g8h9i0j1"
down_revision = "l5e6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "strict_lead_scoping",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "strict_lead_scoping")
