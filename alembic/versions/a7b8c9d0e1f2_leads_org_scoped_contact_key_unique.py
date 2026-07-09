"""leads_org_scoped_contact_key_unique

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-06 00:30:00.000000

Fixes a multi-tenancy bug: `leads.contact_key` was declared with
`unique=True, index=True` (see Column definition history), which SQLAlchemy
implements on Postgres as a single unique index (`ix_leads_contact_key`) —
there is no separately named unique *constraint* to drop, since `unique=True`
+ `index=True` on the same column collapses into one unique index rather than
an index plus a distinct constraint.

That global uniqueness is wrong: two different orgs onboarding a lead with the
same contact_key (e.g. same phone/email-derived slug) collide with an
IntegrityError. This migration:

  1. Drops the old global unique index `ix_leads_contact_key`.
  2. Adds a composite unique constraint `uq_leads_org_contact_key` on
     (org_id, contact_key), matching the Lead model's new __table_args__.
  3. Re-creates a plain (non-unique) index on `contact_key` alone.

Re: whether a lone `contact_key` index is still needed after this change —
yes. Several call sites (e.g. GET /leads/{contact_key}, GET/POST
/memory/{contact_key}[/rebuild], `_gather_contact_calls`, and the historical
`_all_analyses_by_contact` grouping) look up `Lead`/`MemoryBubble` rows by
contact_key alone, without an org filter, because those endpoints don't
(yet) require authentication and so have no org_id to scope by. Until those
are revisited, a plain index on contact_key alone keeps those lookups from
degrading to sequential scans. The composite (org_id, contact_key) unique
constraint's own implicit index does NOT help a contact_key-only lookup on
Postgres (a multi-column btree index can only be used efficiently for
lookups that constrain the leading column(s); org_id is leading here), so
both indexes are kept.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old global unique index on contact_key alone.
    op.drop_index("ix_leads_contact_key", table_name="leads")

    # Composite unique constraint: contact_key only needs to be unique per-org.
    op.create_unique_constraint(
        "uq_leads_org_contact_key", "leads", ["org_id", "contact_key"]
    )

    # Restore a plain (non-unique) index on contact_key alone for the
    # not-yet-org-scoped lookups described above.
    op.create_index(
        op.f("ix_leads_contact_key"), "leads", ["contact_key"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_leads_contact_key"), table_name="leads")
    op.drop_constraint("uq_leads_org_contact_key", "leads", type_="unique")
    op.create_index(
        op.f("ix_leads_contact_key"), "leads", ["contact_key"], unique=True
    )
