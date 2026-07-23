"""add_lead_stage_changes

Revision ID: r1k2l3m4n5o6
Revises: q0j1k2l3m4n5
Create Date: 2026-07-24 00:00:00.000000

Adds lead_stage_changes — an audit trail for every pipeline-stage move.
Backward moves (see dashboard._apply_stage_update) now require a note instead
of being rejected outright; this table is where that note (and every other
stage transition, for a full history) gets stored.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "r1k2l3m4n5o6"
down_revision = "q0j1k2l3m4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_stage_changes",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("lead_id", sa.String(length=255), nullable=False),
        sa.Column("telecaller_id", sa.String(length=255), nullable=True),
        sa.Column("from_stage", sa.String(length=50), nullable=False),
        sa.Column("to_stage", sa.String(length=50), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["telecaller_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lead_stage_changes_org_id"), "lead_stage_changes", ["org_id"])
    op.create_index(op.f("ix_lead_stage_changes_lead_id"), "lead_stage_changes", ["lead_id"])
    op.create_index(
        op.f("ix_lead_stage_changes_telecaller_id"), "lead_stage_changes", ["telecaller_id"]
    )


def downgrade() -> None:
    op.drop_table("lead_stage_changes")
