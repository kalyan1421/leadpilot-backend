"""
One-time repair for the `local://`-vs-`supabase://` storage mismatch (see
leadpilot memory "founder dashboard fixes 2026-07-23"): rows whose audio was
captured by running this backend locally (STORAGE_MODE=local) against the
shared prod DATABASE_URL got a `local://...` audio_file_url, but the file
itself only ever landed in *this machine's* `local_storage/calls/` — never
uploaded anywhere shared. Production (STORAGE_MODE=supabase) can't serve
those rows no matter how the download endpoint dispatches, because the file
simply isn't reachable from Render.

This script uploads every local:// row's file (if still present on this
machine) to Supabase Storage and repoints the DB row at the resulting
`supabase://...` URL, so production can serve it going forward.

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to be set (in .env or the
environment) — the script refuses to run without them rather than silently
no-op'ing.

Usage:
    python scripts/migrate_local_audio_to_supabase.py [--dry-run]

--dry-run lists what would be migrated/skipped without uploading or writing
to the DB.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import SessionLocal
from app.models import AudioCall
from app.utils.local_storage import local_storage_manager


def migrate(dry_run: bool = False):
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set "
            "(leadpilot-backend/.env or the environment) before this can run. Aborting."
        )
        sys.exit(1)

    from app.utils.supabase_storage import SupabaseStorageManager
    supabase_manager = SupabaseStorageManager()

    db = SessionLocal()
    try:
        rows = (
            db.query(AudioCall)
            .filter(AudioCall.audio_file_url.like("local://%"))
            .order_by(AudioCall.timestamp)
            .all()
        )
        if not rows:
            print("No local:// rows found — nothing to migrate.")
            return

        print(f"Found {len(rows)} row(s) with a local:// audio_file_url.\n")

        migrated, missing, failed = [], [], []
        for call in rows:
            call_id = call.call_id
            source_path = local_storage_manager.get_audio_file_path(call_id)

            if not source_path:
                print(f"  [MISSING] {call_id}: no local file at {call.audio_file_url!r} on this machine — unrecoverable here, skipping.")
                missing.append(call_id)
                continue

            if dry_run:
                print(f"  [DRY-RUN] {call_id}: would upload {source_path} -> Supabase Storage")
                migrated.append(call_id)
                continue

            new_url = supabase_manager.save_audio_file(source_path, call_id)
            if not new_url:
                print(f"  [FAILED]  {call_id}: upload to Supabase Storage failed (see log above)")
                failed.append(call_id)
                continue

            call.audio_file_url = new_url
            db.commit()
            print(f"  [OK]      {call_id}: {source_path} -> {new_url}")
            migrated.append(call_id)

        print(
            f"\nDone. migrated={len(migrated)} missing_locally={len(missing)} failed={len(failed)}"
            + (" (dry run — no uploads or DB writes were made)" if dry_run else "")
        )
        if missing:
            print(
                "\nRows in 'missing_locally' have no audio anywhere reachable — the "
                "recording is lost unless the file turns up on whichever machine originally captured it."
            )
    finally:
        db.close()


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
