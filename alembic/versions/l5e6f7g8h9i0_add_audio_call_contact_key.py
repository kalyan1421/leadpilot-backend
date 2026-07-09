"""add_audio_call_contact_key

Revision ID: l5e6f7g8h9i0
Revises: k4d5e6f7g8h9
Create Date: 2026-07-08 00:00:00.000000

Adds audio_calls.contact_key so the join key that groups a call with its lead /
memory bubble is STORED, not re-derived from call_id via regex
(contact_key_from_call_id) at every read site (dashboard.py, calls.py). Storing
it removes the fragile parse and lets the key be phone-based going forward.

Two-phase rollout (IMPORTANT):
  1. Apply THIS migration first (adds the nullable column + backfills existing
     rows from call_id using the same derivation the code uses today). It is
     safe against the running app: the column is nullable and nothing writes it
     yet, so no INSERT breaks whether or not the app has been redeployed.
  2. THEN deploy the code change that (a) adds `contact_key` to the AudioCall
     model, (b) sets `call.contact_key = slug` in upload_recording, and (c)
     switches the read sites to prefer the column. Doing (2) before (1) would
     make every AudioCall INSERT reference a column that doesn't exist yet.

Reversible: downgrade drops the column (no data loss beyond the derived key,
which the code can still recompute from call_id).
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "l5e6f7g8h9i0"
down_revision = "k4d5e6f7g8h9"
branch_labels = None
depends_on = None


def _contact_key_from_call_id(call_id: str) -> str:
    """Mirror of app.utils.memory_bubble.contact_key_from_call_id, inlined so the
    migration doesn't depend on app code that may change after this revision."""
    import re

    s = re.sub(r"^call_", "", call_id or "")
    s = re.sub(r"_[0-9a-f]{6,}$", "", s)   # strip trailing uuid fragment
    s = re.sub(r"_\d+\.\d+$", "", s)       # strip version marker like _1.2
    return s or (call_id or "")


def upgrade() -> None:
    op.add_column("audio_calls", sa.Column("contact_key", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_audio_calls_contact_key"), "audio_calls", ["contact_key"], unique=False)

    # Backfill existing rows from call_id using today's derivation, so the new
    # column matches what the read sites currently compute on the fly.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT call_id FROM audio_calls WHERE contact_key IS NULL")).fetchall()
    for (call_id,) in rows:
        conn.execute(
            sa.text("UPDATE audio_calls SET contact_key = :ck WHERE call_id = :cid"),
            {"ck": _contact_key_from_call_id(call_id), "cid": call_id},
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_audio_calls_contact_key"), table_name="audio_calls")
    op.drop_column("audio_calls", "contact_key")
