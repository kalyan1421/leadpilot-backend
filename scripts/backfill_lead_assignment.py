"""
Backfill `Lead.assigned_to` for every existing lead that predates per-telecaller
ownership (P0-3 of docs/LEAD_ASSIGNMENT_SPEC.md).

WHAT THIS DOES
  For every Lead row where assigned_to IS NULL, looks at that contact's call
  history (AudioCall rows in the same org, matched by the stored
  audio_calls.contact_key column) and assigns the lead to whichever telecaller
  placed the most calls to that contact (ties broken by whoever's most recent
  call is latest). Leads with no call history, or where every matching call has
  a NULL telecaller_id, are left unassigned on purpose — the spec says "don't
  guess." Those leads stay founder/ad_manager-only visible until either a real
  call happens or a founder manually assigns them (P1-1).

WHEN TO RUN THIS
  Run it for an org BEFORE flipping that org's `Organization.strict_lead_scoping`
  flag on (P0-4 — a parallel workstream; that column may not exist in
  app/models.py yet at the time this script is written, so this script does not
  reference or depend on it). Flipping the scoping flag before backfilling would
  make a telecaller's inbox go from "everything" to "empty," which reads as a
  data-loss bug. Backfilling first means most leads already have a sensible
  owner by the time scoping is enabled for that org.

SAFETY / IDEMPOTENCY
  - Only ever touches rows where assigned_to IS NULL, and only ever moves them
    from NULL -> some user id. It never reassigns an already-assigned lead, so
    running it twice (or a hundred times) is a no-op after the first pass:
    nothing left to backfill, nothing changes.
  - Before writing, the picked telecaller_id is re-verified against the users
    table (must still exist, must be in the same org as the lead) so
    stale/orphaned telecaller_id values on old AudioCall rows can't leak a
    cross-org or deleted-user assignment onto a lead. Role isn't re-checked —
    a user who placed the call as a telecaller and was later promoted is still
    a legitimate historical owner of that lead.
  - Commits per-org, not one giant transaction, so a failure partway through a
    large multi-org run doesn't lose already-completed orgs and doesn't hold a
    long-running lock against the live app.
  - Default is a dry run in spirit only in that you MUST pass --dry-run to get
    one; without it, the script commits. Always run with --dry-run first and
    read the per-org summary before running for real.

USAGE
  python scripts/backfill_lead_assignment.py --dry-run              # preview, all orgs
  python scripts/backfill_lead_assignment.py --dry-run --org-id X   # preview one org
  python scripts/backfill_lead_assignment.py --org-id X             # write, one org
  python scripts/backfill_lead_assignment.py                        # write, ALL orgs

DO NOT run this against production without a human reviewing the --dry-run
output first (see docs/LEAD_ASSIGNMENT_SPEC.md, "Rollout & Migration Risk").
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import argparse
import logging
from collections import defaultdict

import app.database  # noqa: F401
try:
    app.database.engine.echo = False
except Exception:
    pass
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.database import SessionLocal
from app.models import AudioCall, Lead, Organization, User


def _pick_winner(db, org_id: str, contact_key: str):
    """Return the telecaller_id with the most AudioCall rows for this org+contact,
    tie-broken by most recent AudioCall.timestamp. Returns None if there's no
    signal (no matching calls, or all matching calls have a NULL telecaller_id)."""
    calls = (
        db.query(AudioCall.telecaller_id, AudioCall.timestamp)
        .filter(
            AudioCall.org_id == org_id,
            AudioCall.contact_key == contact_key,
            AudioCall.telecaller_id.isnot(None),
        )
        .all()
    )
    if not calls:
        return None

    counts = defaultdict(int)
    most_recent = {}
    for telecaller_id, ts in calls:
        counts[telecaller_id] += 1
        if telecaller_id not in most_recent or (ts is not None and ts > most_recent[telecaller_id]):
            most_recent[telecaller_id] = ts

    # Most calls wins; ties broken by most recent call timestamp.
    winner = max(counts.items(), key=lambda kv: (kv[1], most_recent.get(kv[0])))
    return winner[0]


def _verify_telecaller(db, telecaller_id: str, org_id: str) -> bool:
    """Guard against stale/orphaned AudioCall.telecaller_id values: the picked
    user must still exist and belong to the same org as the lead. (Doesn't
    require role == 'telecaller' — if a user's role changed since they placed
    the call, they're still a legitimate owner of leads they historically
    worked; scoping only cares about assigned_to, not the assignee's current role.)"""
    user = (
        db.query(User)
        .filter(User.id == telecaller_id, User.org_id == org_id)
        .first()
    )
    return user is not None


def backfill_org(db, org: Organization, dry_run: bool) -> dict:
    leads = db.query(Lead).filter(Lead.org_id == org.id, Lead.assigned_to.is_(None)).all()

    examined = len(leads)
    backfilled = 0
    unassigned = 0
    per_telecaller = defaultdict(int)

    for lead in leads:
        winner = _pick_winner(db, org.id, lead.contact_key)
        if winner is None:
            unassigned += 1
            continue
        if not _verify_telecaller(db, winner, org.id):
            # Stale/orphaned telecaller_id (deleted user, or somehow a different
            # org) -- treat exactly like "no signal" rather than guessing.
            unassigned += 1
            continue

        lead.assigned_to = winner
        backfilled += 1
        per_telecaller[winner] += 1

    if backfilled and not dry_run:
        db.commit()
    else:
        db.rollback()

    return {
        "org_id": org.id,
        "org_name": org.name,
        "examined": examined,
        "backfilled": backfilled,
        "unassigned": unassigned,
        "per_telecaller": dict(per_telecaller),
    }


def _print_summary(result: dict, dry_run: bool):
    verb = "WOULD backfill" if dry_run else "backfilled"
    print(f"\nOrg {result['org_id']} ({result['org_name']}):")
    print(f"  leads examined (assigned_to IS NULL): {result['examined']}")
    print(f"  {verb}: {result['backfilled']}")
    print(f"  left unassigned (no signal): {result['unassigned']}")
    if result["per_telecaller"]:
        print("  newly-assigned leads by telecaller:")
        for telecaller_id, count in sorted(result["per_telecaller"].items(), key=lambda kv: -kv[1]):
            print(f"    {telecaller_id}: {count}")
    else:
        print("  newly-assigned leads by telecaller: (none)")


def run(org_id: str | None, dry_run: bool):
    db = SessionLocal()
    try:
        query = db.query(Organization)
        if org_id:
            query = query.filter(Organization.id == org_id)
        orgs = query.order_by(Organization.id.asc()).all()

        if not orgs:
            print(f"No matching organizations found (org_id={org_id!r}). Nothing to do.")
            return

        print(f"Processing {len(orgs)} org(s){' (dry run — no writes)' if dry_run else ''}...")

        totals = {"examined": 0, "backfilled": 0, "unassigned": 0}
        for org in orgs:
            result = backfill_org(db, org, dry_run)
            _print_summary(result, dry_run)
            totals["examined"] += result["examined"]
            totals["backfilled"] += result["backfilled"]
            totals["unassigned"] += result["unassigned"]

        print("\n" + "=" * 60)
        print(f"TOTAL across {len(orgs)} org(s): examined={totals['examined']} "
              f"{'would_backfill' if dry_run else 'backfilled'}={totals['backfilled']} "
              f"unassigned={totals['unassigned']}")
        if dry_run:
            print("Dry run only — no changes were committed. Re-run without --dry-run to write.")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Backfill Lead.assigned_to from call history, before enabling "
        "per-org strict lead scoping (see docs/LEAD_ASSIGNMENT_SPEC.md P0-3)."
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute and print what would change per org, without committing anything. Off by default.",
    )
    ap.add_argument(
        "--org-id",
        default=None,
        help="Only process this org (matches Organization.id). Default: process all orgs.",
    )
    args = ap.parse_args()
    run(org_id=args.org_id, dry_run=args.dry_run)
