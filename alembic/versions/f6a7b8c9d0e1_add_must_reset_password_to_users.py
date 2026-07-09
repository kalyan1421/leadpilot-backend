"""add_must_reset_password_to_users

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-06 00:00:00.000000

Adds users.must_reset_password — flips true on invite (POST /api/team/invite)
and admin-triggered reset (POST /api/team/{user_id}/reset-password), flips
false once the user completes POST /api/auth/change-password. Lets clients
force a "set a new password" screen instead of letting a manually-shared temp
password persist unchanged indefinitely.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_reset_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "must_reset_password")
