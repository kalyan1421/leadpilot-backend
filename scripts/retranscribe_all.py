"""
Re-transcribe every stored call through Sarvam diarization, then re-analyse + rebuild memory.
Upgrades calls imported before Sarvam (single-speaker transcripts, all 'AGENT') to proper
2-speaker (AGENT/USER) diarization. Preserves call_ids.

Usage:  python scripts/retranscribe_all.py [--force]
  --force  also redoes calls that are already diarized (have speaker_id)
"""
import os, sys, time; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uuid
from collections import Counter

from app.database import SessionLocal
from app.models import AudioCall, LeadAnalysis
from app.utils.s3 import get_storage_manager
from app.utils.sarvam import transcribe_file
from app.utils.lead_analyzer import analyze_call
from app.utils.memory_bubble import contact_key_from_call_id

_FIELDS = ("bant_score", "bant_breakdown", "lead_verdict", "lead_verdict_reason", "sentiment_arc",
           "intent_tags", "entities", "call_summary", "key_points", "next_steps", "next_action", "agent_debrief")


def _transcribe_with_retry(path, attempts: int = 3):
    """Transcription is the network-flaky step; retry transient errors (DNS/connect drops)
    with linear backoff so one blip doesn't abort the whole batch."""
    last = None
    for n in range(1, attempts + 1):
        try:
            return transcribe_file(path)
        except Exception as e:                       # noqa: BLE001 — transient network errors
            last = e
            print(f"  transcribe attempt {n}/{attempts} failed: {type(e).__name__}: {e}")
            if n < attempts:
                time.sleep(2 * n)
    raise last


def main(force: bool = False):
    from app.api.calls import _build_and_store_memory
    manager = get_storage_manager()
    db = SessionLocal()
    done = failed = skipped = 0
    try:
        calls = db.query(AudioCall).order_by(AudioCall.timestamp.asc()).all()
        print(f"Scanning {len(calls)} calls...")
        for i, call in enumerate(calls, 1):
            try:
                turns = (call.transcript or {}).get("turns") if isinstance(call.transcript, dict) else None
                diarized = bool(turns) and any("speaker_id" in t for t in turns)
                rec0 = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call.call_id).first()
                analysed = bool(rec0) and rec0.status == "completed"
                already = diarized and analysed   # only skip fully-processed calls (so partial/failed get retried)
                path = manager.get_audio_file_path(call.call_id)
                if not path or not os.path.exists(path):
                    print(f"[{i}/{len(calls)}] {call.call_id}: no local audio — skip"); skipped += 1; continue
                if already and not force:
                    print(f"[{i}/{len(calls)}] {call.call_id}: already diarized + analysed — skip"); skipped += 1; continue
                print(f"[{i}/{len(calls)}] {call.call_id}: re-transcribing (Sarvam diarized)...")
                r = _transcribe_with_retry(path)
                tns = r.get("turns") or []
                if not tns:
                    print(f"  no turns ({r.get('error')}) — skip"); failed += 1; continue
                call.transcript = {"turns": tns, "full_text": r.get("full_text", ""),
                                   "language": r.get("language", "unknown"), "quality": r.get("quality", "ok")}
                db.commit()
                a = analyze_call(call.transcript)
                if not a:
                    print("  analysis failed — transcript saved, skipping scoring"); failed += 1; continue
                rec = rec0 or LeadAnalysis(id=str(uuid.uuid4()), call_id=call.call_id)
                if rec0 is None:
                    db.add(rec)
                for k in _FIELDS:
                    setattr(rec, k, a.get(k))
                rec.status, rec.error = "completed", None
                db.commit()
                try:
                    _build_and_store_memory(contact_key_from_call_id(call.call_id), db)
                except Exception as e:
                    print(f"  memory rebuild skipped: {e}")
                print(f"  roles={dict(Counter(t['role'] for t in tns))} lang={r.get('language')} "
                      f"verdict={a.get('lead_verdict')} bant={a.get('bant_score')}")
                done += 1
            except Exception as e:                   # noqa: BLE001 — isolate per-call failures
                db.rollback()
                print(f"  ERROR on {call.call_id}: {type(e).__name__}: {e} — continuing")
                failed += 1
                continue
        print(f"\nDONE. processed={done} failed={failed} skipped={skipped} total={len(calls)}")
        return failed
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(1 if main(force="--force" in sys.argv) else 0)
