"""add_audio_call_content_hash

Revision ID: k4d5e6f7g8h9
Revises: j3c4d5e6f7g8
Create Date: 2026-07-07 21:31:00.000000

Adds audio_calls.content_hash (SHA-256 of the uploaded bytes) so /upload can
detect a retry/double-tap re-sending the exact same recording and return the
existing call_id instead of creating a duplicate Lead/AudioCall/ProcessingJob.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "k4d5e6f7g8h9"
down_revision = "j3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_calls", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_audio_calls_content_hash"), "audio_calls", ["content_hash"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audio_calls_content_hash"), table_name="audio_calls")
    op.drop_column("audio_calls", "content_hash")
