"""widen_org_logo_url_to_text

Revision ID: o8h9i0j1k2l3
Revises: n7g8h9i0j1k2
Create Date: 2026-07-11 00:00:00.000000

Widens organizations.logo_url from VARCHAR(500) to TEXT.

The founder onboarding wizard and the org settings page upload the company
logo as an inline base64 data URL (leadpilot-web reads the file with
FileReader.readAsDataURL and sends the result straight through as logo_url).
Even a small image encodes to well over 500 characters, so every logo upload
was failing — a 422 "String should have at most 500 characters" that surfaced
in the UI as "Couldn't save your organisation profile. Please try again." and
blocked org creation. See also the matching schema/model change lifting the
500-char cap.

Type-only widening: TEXT is a strict superset of VARCHAR(500), so no existing
value can be truncated or lost, and the change is safe under a running old
app instance (it never wrote anything longer than 500 anyway).

Reversible: downgrade narrows back to VARCHAR(500). That would fail if any row
already holds a longer value (an actual uploaded logo), which is expected —
you cannot un-store data that no longer fits.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "o8h9i0j1k2l3"
down_revision = "n7g8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "organizations",
        "logo_url",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "organizations",
        "logo_url",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=True,
    )
