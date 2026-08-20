"""add founder dashboard notifications"""

import sqlalchemy as sa

from alembic import op


revision = "t3m4n5o6p7q8"
down_revision = "s2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # The API currently calls Base.metadata.create_all() at startup. If that
    # runs before Alembic on an existing environment, the model-created table
    # is already present with the same columns/constraints. Reconcile that
    # harmlessly instead of failing with DuplicateTable.
    if "notifications" not in inspector.get_table_names():
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(length=255), nullable=False),
            sa.Column("org_id", sa.String(length=255), nullable=False),
            sa.Column("type", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False, server_default="info"),
            sa.Column("entity_type", sa.String(length=50), nullable=True),
            sa.Column("entity_id", sa.String(length=255), nullable=True),
            sa.Column("actor_name", sa.String(length=255), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        columns = {column["name"]: column for column in inspector.get_columns("notifications")}
        required = {"id", "org_id", "type", "title", "message", "severity", "entity_type", "entity_id", "actor_name", "read_at", "created_at"}
        missing = required - columns.keys()
        if missing:
            raise RuntimeError(f"notifications table is missing required columns: {sorted(missing)}")
        if columns["severity"].get("default") is None:
            op.alter_column("notifications", "severity", server_default=sa.text("'info'"))

    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("notifications")}
    for index_name, column_name in (
        (op.f("ix_notifications_org_id"), "org_id"),
        (op.f("ix_notifications_type"), "type"),
        (op.f("ix_notifications_created_at"), "created_at"),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, "notifications", [column_name])


def downgrade() -> None:
    op.drop_table("notifications")
