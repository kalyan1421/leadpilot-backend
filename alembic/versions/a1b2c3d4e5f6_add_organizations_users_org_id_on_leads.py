"""add_organizations_users_org_id_on_leads

Revision ID: a1b2c3d4e5f6
Revises: 20c47af31c71
Create Date: 2026-07-03 00:00:00.000000

Note: on this developer's local DB these tables were already created by
Base.metadata.create_all() (the app's existing startup-time schema bootstrap) before
this migration was written — alembic_version isn't stamped locally, so this file exists
for a clean deploy environment (Railway/CI) that runs `alembic upgrade head` from empty.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "20c47af31c71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_organizations_id"), "organizations", ["id"])
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"])
    op.create_index(op.f("ix_users_org_id"), "users", ["org_id"])
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.add_column("leads", sa.Column("org_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_leads_org_id"), "leads", ["org_id"])
    op.create_foreign_key(None, "leads", "organizations", ["org_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(None, "leads", type_="foreignkey")
    op.drop_index(op.f("ix_leads_org_id"), table_name="leads")
    op.drop_column("leads", "org_id")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_org_id"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")

    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_id"), table_name="organizations")
    op.drop_table("organizations")
