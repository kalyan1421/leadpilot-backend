"""backfill_lead_analysis_org_id

Revision ID: i2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-07-07 00:00:02.000000

Data-only fix: the upload pipeline's background job (app/api/calls.py,
_process_upload_job) has never stamped `org_id` on `LeadAnalysis` — only the
manual re-run endpoint (POST .../lead-analysis) did. Combined with adding
org_id filtering to GET/PATCH .../lead-analysis in this same session (closing
a cross-tenant leak), this meant the vast majority of real analyses — created
via the actual upload flow, not the manual endpoint — started 404'ing for
their own org. The endpoint-side fix now checks ownership via AudioCall
instead of LeadAnalysis.org_id directly, but this backfills the column itself
too so it isn't silently wrong for any future direct query against it.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "i2b3c4d5e6f7"
down_revision = "h1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE lead_analysis
        SET org_id = audio_calls.org_id
        FROM audio_calls
        WHERE lead_analysis.call_id = audio_calls.call_id
          AND lead_analysis.org_id IS NULL
          AND audio_calls.org_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Data backfill is not reversible (we don't know which rows were NULL
    # before), and reverting it would just reintroduce the bug — no-op.
    pass
