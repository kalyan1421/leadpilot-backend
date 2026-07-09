"""add_follow_ups_table

Revision ID: h1a2b3c4d5e6
Revises: g7a8b9c0d1e2
Create Date: 2026-07-07 00:00:01.000000

Adds the `follow_ups` table — previously follow-ups only existed in the
Flutter app's on-device SharedPreferences (LocalFollowUpStore), with no
backend endpoint at all. Feeds the PRD's "missed follow-up rate" leakage
metric once the client is wired to sync here.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "h1a2b3c4d5e6"
down_revision = "g7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "follow_ups",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("org_id", sa.String(length=255), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("lead_id", sa.String(length=255), sa.ForeignKey("leads.id"), nullable=True),
        sa.Column("telecaller_id", sa.String(length=255), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_follow_ups_id"), "follow_ups", ["id"], unique=False)
    op.create_index(op.f("ix_follow_ups_org_id"), "follow_ups", ["org_id"], unique=False)
    op.create_index(op.f("ix_follow_ups_lead_id"), "follow_ups", ["lead_id"], unique=False)
    op.create_index(op.f("ix_follow_ups_telecaller_id"), "follow_ups", ["telecaller_id"], unique=False)
    op.create_index(op.f("ix_follow_ups_due_at"), "follow_ups", ["due_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_follow_ups_due_at"), table_name="follow_ups")
    op.drop_index(op.f("ix_follow_ups_telecaller_id"), table_name="follow_ups")
    op.drop_index(op.f("ix_follow_ups_lead_id"), table_name="follow_ups")
    op.drop_index(op.f("ix_follow_ups_org_id"), table_name="follow_ups")
    op.drop_index(op.f("ix_follow_ups_id"), table_name="follow_ups")
    op.drop_table("follow_ups")
