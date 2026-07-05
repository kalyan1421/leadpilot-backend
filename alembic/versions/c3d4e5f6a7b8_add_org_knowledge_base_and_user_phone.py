"""add_org_knowledge_base_and_user_phone

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-04 00:00:00.000000

Adds the Organisation Knowledge Base fields (industry, website_url, services,
pricing range, target_audience, competitors, brand_voice, languages, usps) that
the founder onboarding wizard already collects but never persisted, plus
User.phone (needed for telecaller invites / future phone-based login).
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("industry", sa.String(length=100), nullable=True))
    op.add_column("organizations", sa.Column("website_url", sa.String(length=500), nullable=True))
    op.add_column("organizations", sa.Column("services", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("pricing_min", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("pricing_max", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("target_audience", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("competitors", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("brand_voice", sa.String(length=50), nullable=True))
    op.add_column("organizations", sa.Column("languages", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("usps", sa.JSON(), nullable=True))

    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_phone"), table_name="users")
    op.drop_column("users", "phone")

    op.drop_column("organizations", "usps")
    op.drop_column("organizations", "languages")
    op.drop_column("organizations", "brand_voice")
    op.drop_column("organizations", "competitors")
    op.drop_column("organizations", "target_audience")
    op.drop_column("organizations", "pricing_max")
    op.drop_column("organizations", "pricing_min")
    op.drop_column("organizations", "services")
    op.drop_column("organizations", "website_url")
    op.drop_column("organizations", "industry")
