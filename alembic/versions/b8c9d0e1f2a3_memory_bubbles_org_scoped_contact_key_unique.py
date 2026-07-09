"""memory_bubbles_org_scoped_contact_key_unique

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-06 01:00:00.000000

Same multi-tenancy bug as leads.contact_key (see a7b8c9d0e1f2), on the
sibling table: `memory_bubbles.contact_key` was declared with
`unique=True, index=True` (see Column definition history), which SQLAlchemy
implements on Postgres as a single unique index (`ix_memory_bubbles_contact_key`)
— there is no separately named unique *constraint* to drop, since
`unique=True` + `index=True` on the same column collapses into one unique
index rather than an index plus a distinct constraint.

That global uniqueness is wrong: two different orgs whose contacts happen to
derive the same contact_key (e.g. same phone/name-slug) collide with an
IntegrityError. This migration:

  1. Drops the old global unique index `ix_memory_bubbles_contact_key`.
  2. Adds a composite unique constraint `uq_memory_bubbles_org_contact_key` on
     (org_id, contact_key), matching the MemoryBubble model's new
     __table_args__ (and mirroring uq_leads_org_contact_key).
  3. Re-creates a plain (non-unique) index on `contact_key` alone.

Re: whether a lone `contact_key` index is still needed after this change —
yes, same reasoning as the leads migration. GET /memory/{contact_key},
POST /memory/{contact_key}/rebuild, and GET /leads/{contact_key} all fall
back to an unscoped contact_key-only lookup when the caller sends no bearer
token (Flutter doesn't always send one yet), so a plain index on contact_key
alone keeps those lookups from degrading to sequential scans. The composite
(org_id, contact_key) unique constraint's own implicit index does NOT help a
contact_key-only lookup on Postgres (a multi-column btree index can only be
used efficiently for lookups that constrain the leading column(s); org_id is
leading here), so both indexes are kept.

IMPORTANT — pre-migration data check required: existing memory_bubbles rows
likely have org_id = NULL (same as leads did before its fix). On Postgres,
a UNIQUE constraint treats NULL as distinct from every other NULL, so
multiple (NULL, 'same_contact_key') rows would NOT violate the new composite
constraint even though they logically collide. This migration will not fail
even if such duplicate-contact_key/NULL-org_id rows already exist — but that
also means the constraint silently does NOT protect those rows going
forward. Recommended before/after running this migration: run
    SELECT contact_key, count(*) FROM memory_bubbles
    WHERE org_id IS NULL GROUP BY contact_key HAVING count(*) > 1;
to check whether a backfill (assigning the correct org_id to each row) is
needed. Do not assume the backfill is unnecessary without running this check.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old global unique index on contact_key alone.
    op.drop_index("ix_memory_bubbles_contact_key", table_name="memory_bubbles")

    # Composite unique constraint: contact_key only needs to be unique per-org.
    op.create_unique_constraint(
        "uq_memory_bubbles_org_contact_key", "memory_bubbles", ["org_id", "contact_key"]
    )

    # Restore a plain (non-unique) index on contact_key alone for the
    # not-yet-org-scoped lookups described above.
    op.create_index(
        op.f("ix_memory_bubbles_contact_key"), "memory_bubbles", ["contact_key"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_memory_bubbles_contact_key"), table_name="memory_bubbles")
    op.drop_constraint("uq_memory_bubbles_org_contact_key", "memory_bubbles", type_="unique")
    op.create_index(
        op.f("ix_memory_bubbles_contact_key"), "memory_bubbles", ["contact_key"], unique=True
    )
