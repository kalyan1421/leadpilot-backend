"""add_user_fcm_token

Revision ID: s2l3m4n5o6p7
Revises: r1k2l3m4n5o6
Create Date: 2026-08-20 00:00:00.000000

Adds users.fcm_token — the device's Firebase Cloud Messaging registration
token, so the backend can push a real notification to the telecaller app
(new lead assigned, founder-driven stage change, password reset) instead of
the app only ever showing reminders it scheduled for itself. One token per
user (nullable until the app registers one); a fresh login/token refresh
just overwrites it. See app/utils/push_notifications.py.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "s2l3m4n5o6p7"
down_revision = "r1k2l3m4n5o6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("fcm_token", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "fcm_token")
