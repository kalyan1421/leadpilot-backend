"""add_org_scoping_and_lead_pipeline

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03 00:00:00.000000

Adds org_id (and telecaller_id where relevant) to every table that previously had
no tenant/owner column: audio_calls, processing_jobs, lead_analysis, memory_bubbles.
Also adds Lead.assigned_to and Lead.pipeline_stage for the founder kanban board.
All new columns are nullable — existing rows have neither, and the upload path only
stamps org_id/telecaller_id when a bearer token is present (Flutter auth isn't
wired yet), so this can't be NOT NULL until that's confirmed.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_calls", sa.Column("org_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_audio_calls_org_id"), "audio_calls", ["org_id"])
    op.create_foreign_key(None, "audio_calls", "organizations", ["org_id"], ["id"])

    op.add_column("audio_calls", sa.Column("telecaller_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_audio_calls_telecaller_id"), "audio_calls", ["telecaller_id"])
    op.create_foreign_key(None, "audio_calls", "users", ["telecaller_id"], ["id"])

    op.add_column("processing_jobs", sa.Column("org_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_processing_jobs_org_id"), "processing_jobs", ["org_id"])
    op.create_foreign_key(None, "processing_jobs", "organizations", ["org_id"], ["id"])

    op.add_column("lead_analysis", sa.Column("org_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_lead_analysis_org_id"), "lead_analysis", ["org_id"])
    op.create_foreign_key(None, "lead_analysis", "organizations", ["org_id"], ["id"])

    op.add_column("memory_bubbles", sa.Column("org_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_memory_bubbles_org_id"), "memory_bubbles", ["org_id"])
    op.create_foreign_key(None, "memory_bubbles", "organizations", ["org_id"], ["id"])

    op.add_column("leads", sa.Column("assigned_to", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_leads_assigned_to"), "leads", ["assigned_to"])
    op.create_foreign_key(None, "leads", "users", ["assigned_to"], ["id"])

    op.add_column(
        "leads",
        sa.Column("pipeline_stage", sa.String(length=30), nullable=False, server_default="New"),
    )
    op.create_index(op.f("ix_leads_pipeline_stage"), "leads", ["pipeline_stage"])


def downgrade() -> None:
    op.drop_index(op.f("ix_leads_pipeline_stage"), table_name="leads")
    op.drop_column("leads", "pipeline_stage")

    op.drop_constraint(None, "leads", type_="foreignkey")
    op.drop_index(op.f("ix_leads_assigned_to"), table_name="leads")
    op.drop_column("leads", "assigned_to")

    op.drop_constraint(None, "memory_bubbles", type_="foreignkey")
    op.drop_index(op.f("ix_memory_bubbles_org_id"), table_name="memory_bubbles")
    op.drop_column("memory_bubbles", "org_id")

    op.drop_constraint(None, "lead_analysis", type_="foreignkey")
    op.drop_index(op.f("ix_lead_analysis_org_id"), table_name="lead_analysis")
    op.drop_column("lead_analysis", "org_id")

    op.drop_constraint(None, "processing_jobs", type_="foreignkey")
    op.drop_index(op.f("ix_processing_jobs_org_id"), table_name="processing_jobs")
    op.drop_column("processing_jobs", "org_id")

    op.drop_constraint(None, "audio_calls", type_="foreignkey")
    op.drop_index(op.f("ix_audio_calls_telecaller_id"), table_name="audio_calls")
    op.drop_column("audio_calls", "telecaller_id")

    op.drop_constraint(None, "audio_calls", type_="foreignkey")
    op.drop_index(op.f("ix_audio_calls_org_id"), table_name="audio_calls")
    op.drop_column("audio_calls", "org_id")
