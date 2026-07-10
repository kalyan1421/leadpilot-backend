"""add_precall_brief

Revision ID: n7g8h9i0j1k2
Revises: m6f7g8h9i0j1
Create Date: 2026-07-10 00:00:00.000000

Adds memory_bubbles.pre_call_brief (+ generated_at + total_calls snapshot) —
the cached output of app/utils/precall_brief.py: opening line, key points,
script steps, objection responses, and a dynamic checklist, all grounded in
the org's KB and this contact's memory bubble. Generated lazily by
get_lead_detail and re-generated whenever a new call lands for the contact
(detected via pre_call_brief_total_calls != total_calls, which sidesteps
relying on updated_at ordering against the same commit that writes the brief).

Additive, nullable columns — every existing row simply has no cached brief
yet until its lead is next opened, which is a no-op difference from today
(the Pre-Call screen already renders nothing/hardcoded fallbacks for these
fields).

Reversible: downgrade drops all three columns; the cache is only ever
derived data, never the source of truth.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "n7g8h9i0j1k2"
down_revision = "m6f7g8h9i0j1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_bubbles", sa.Column("pre_call_brief", sa.JSON(), nullable=True))
    op.add_column(
        "memory_bubbles",
        sa.Column("pre_call_brief_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_bubbles", sa.Column("pre_call_brief_total_calls", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("memory_bubbles", "pre_call_brief_total_calls")
    op.drop_column("memory_bubbles", "pre_call_brief_generated_at")
    op.drop_column("memory_bubbles", "pre_call_brief")
