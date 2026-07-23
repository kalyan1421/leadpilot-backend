"""add_call_log

Revision ID: q0j1k2l3m4n5
Revises: p9i0j1k2l3m4
Create Date: 2026-07-23 00:00:00.000000

Adds call_logs — one row per raw phone call from a telecaller's device call
log (every inbound/outbound/missed call, not just calls placed through the
app's own dialer button). Separate from audio_calls: most call-log entries
never get a recording or AI analysis.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "q0j1k2l3m4n5"
down_revision = "p9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_logs",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("telecaller_id", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_call_id", sa.String(length=128), nullable=True),
        sa.Column("audio_call_id", sa.String(length=255), nullable=True),
        sa.Column("lead_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["telecaller_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["audio_call_id"], ["audio_calls.call_id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telecaller_id", "device_call_id", name="uq_call_logs_telecaller_device_call"
        ),
    )
    op.create_index(op.f("ix_call_logs_org_id"), "call_logs", ["org_id"])
    op.create_index(op.f("ix_call_logs_telecaller_id"), "call_logs", ["telecaller_id"])
    op.create_index(op.f("ix_call_logs_phone"), "call_logs", ["phone"])
    op.create_index(op.f("ix_call_logs_called_at"), "call_logs", ["called_at"])
    op.create_index(op.f("ix_call_logs_device_call_id"), "call_logs", ["device_call_id"])
    op.create_index(op.f("ix_call_logs_audio_call_id"), "call_logs", ["audio_call_id"])
    op.create_index(op.f("ix_call_logs_lead_id"), "call_logs", ["lead_id"])


def downgrade() -> None:
    op.drop_table("call_logs")
