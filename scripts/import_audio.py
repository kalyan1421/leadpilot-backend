"""
Bulk-ingest audio files from the Audio/ folder through the current Sarvam pipeline
(transcribe + diarize -> analyse -> memory). Idempotent by filename slug.

Usage:  python scripts/import_audio.py [--force]
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import glob
import re
import uuid
from datetime import datetime

from app.config import settings
from app.database import SessionLocal
from app.models import AudioCall, Lead
from app.utils.s3 import get_storage_manager
from app.utils.memory_bubble import slugify_contact
from app.api.calls import _process_uploaded_recording


def import_audio_files(force: bool = False):
    manager = get_storage_manager()
    audio_dir = settings.audio_source_path
    files = [f for f in glob.glob(os.path.join(audio_dir, "*"))
             if f.lower().endswith((".mp3", ".mpeg", ".wav", ".m4a", ".flac"))]
    if not files:
        print(f"No audio files found in {audio_dir}")
        return
    print(f"Found {len(files)} audio files.")

    db = SessionLocal()
    try:
        for path in files:
            name = os.path.basename(path).replace(".mp3.mpeg", "").rsplit(".", 1)[0]
            slug = slugify_contact(name)
            # Idempotency: a call_id is exactly f"call_{slug}_{uuid8}". Match the uuid
            # suffix precisely — a plain LIKE "call_{slug}_%" makes slug "call_recording"
            # collide with "call_recording_3", wrongly skipping the base recording.
            if not force:
                exact = re.compile(rf"^call_{re.escape(slug)}_[0-9a-f]{{6,}}$")
                rows = db.query(AudioCall.call_id).filter(AudioCall.call_id.like(f"call_{slug}_%")).all()
                if any(exact.match(cid) for (cid,) in rows):
                    print(f"  {name}: already imported - skip")
                    continue
            call_id = f"call_{slug}_{uuid.uuid4().hex[:8]}"
            audio_url = manager.save_audio_file(path, call_id)
            if not db.query(Lead).filter(Lead.contact_key == slug).first():
                db.add(Lead(id=str(uuid.uuid4()), contact_key=slug,
                            name=name.replace("_", " ").title(), status="contacted"))
            db.add(AudioCall(call_id=call_id, timestamp=datetime.utcnow(),
                             transcript={"turns": []}, audio_file_url=audio_url))
            db.commit()
            stored = manager.get_audio_file_path(call_id) or path
            print(f"  {name} -> {call_id}: running Sarvam pipeline (transcribe -> analyse -> memory)...")
            _process_uploaded_recording(call_id, stored)
    finally:
        db.close()


if __name__ == "__main__":
    import_audio_files(force="--force" in sys.argv)
