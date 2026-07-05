"""add_attendance_table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-04 00:00:00.000000

Adds the `attendance` table backing telecaller check-in/check-out — timestamp
only (no geolocation, no photo; that's an explicit product decision). One row
per user per calendar day, enforced by a unique (user_id, date) constraint.

This mirrors DDL already applied directly to the Supabase project via MCP, so
local dev and Supabase stay in sync.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("org_id", sa.String(length=255), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(length=255), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "date", name="uq_attendance_user_date"),
    )
    op.create_index(op.f("ix_attendance_id"), "attendance", ["id"], unique=False)
    op.create_index(op.f("ix_attendance_org_id"), "attendance", ["org_id"], unique=False)
    op.create_index(op.f("ix_attendance_user_id"), "attendance", ["user_id"], unique=False)
    op.create_index(op.f("ix_attendance_date"), "attendance", ["date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_attendance_date"), table_name="attendance")
    op.drop_index(op.f("ix_attendance_user_id"), table_name="attendance")
    op.drop_index(op.f("ix_attendance_org_id"), table_name="attendance")
    op.drop_index(op.f("ix_attendance_id"), table_name="attendance")
    op.drop_table("attendance")
