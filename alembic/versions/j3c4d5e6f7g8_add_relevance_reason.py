"""add_relevance_reason

Revision ID: j3c4d5e6f7g8
Revises: i2b3c4d5e6f7
Create Date: 2026-07-07 21:30:00.000000

Adds lead_analysis.relevance_reason — the AI's explanation for why it judged a
call not_relevant (relevance filter). Previously computed by the analyzer but
never persisted, so a not-relevant verdict was unexplainable after the fact.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "j3c4d5e6f7g8"
down_revision = "i2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_analysis", sa.Column("relevance_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_analysis", "relevance_reason")
