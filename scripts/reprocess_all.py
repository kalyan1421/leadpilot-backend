"""
Re-run the AI lead-analysis for every stored call — IN PARALLEL — using the configured
reasoning provider (REASONING_PROVIDER in .env). Uses each call's STORED transcript, so it
does NOT re-transcribe. Run after a model / prompt / provider change to refresh stored
scores, evidence, and verdicts.

Usage:
  python scripts/reprocess_all.py [--workers N] [--memory]
    --workers N   number of calls analysed concurrently   (default 5)
    --memory      also rebuild per-contact memory bubbles afterwards (slower; off by default)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import argparse
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import app.database  # noqa: F401
try:
    app.database.engine.echo = False
except Exception:
    pass
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.database import SessionLocal
from app.models import AudioCall, LeadAnalysis
from app.utils.lead_analyzer import analyze_call

_FIELDS = ("bant_score", "bant_breakdown", "lead_verdict", "lead_verdict_reason", "sentiment_arc",
           "intent_tags", "entities", "call_summary", "key_points", "next_steps", "next_action", "agent_debrief")


def _one(call_id: str):
    """Analyse a single call in its OWN DB session (SQLAlchemy sessions aren't thread-safe)."""
    db = SessionLocal()
    try:
        call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
        turns = call.transcript.get("turns") if (call and isinstance(call.transcript, dict)) else None
        if not turns:
            return (call_id, "skip", "no transcript")
        a = analyze_call(call.transcript)
        if not a:
            return (call_id, "fail", "analysis returned None")
        rec = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
        if not rec:
            rec = LeadAnalysis(id=str(uuid.uuid4()), call_id=call_id)
            db.add(rec)
        for k in _FIELDS:
            setattr(rec, k, a.get(k))
        rec.status, rec.error = "completed", None
        db.commit()
        agent = (a.get("agent_debrief") or {}).get("total_score")
        return (call_id, "ok", f"{a.get('lead_verdict')}  bant={a.get('bant_score')}  agent={agent}")
    except Exception as e:  # noqa: BLE001
        return (call_id, "fail", str(e)[:100])
    finally:
        db.close()


def reprocess_all(workers: int = 5, memory: bool = False):
    db = SessionLocal()
    ids = [c.call_id for c in db.query(AudioCall).order_by(AudioCall.timestamp.asc()).all()]
    db.close()
    print(f"Reprocessing {len(ids)} calls with {workers} parallel workers "
          f"(provider = analysis via .env)...")

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, cid): cid for cid in ids}
        for i, fut in enumerate(as_completed(futs), 1):
            cid, st, info = fut.result()
            results.append((cid, st, info))
            print(f"[{i}/{len(ids)}] {st.upper():4} {cid}: {info}")

    ok = sum(1 for _, s, _ in results if s == "ok")
    fail = sum(1 for _, s, _ in results if s == "fail")
    skip = sum(1 for _, s, _ in results if s == "skip")
    print(f"\nDONE. ok={ok} fail={fail} skip={skip} of {len(ids)}")
    for cid, s, info in results:
        if s == "fail":
            print(f"  FAILED {cid}: {info}")

    if memory:
        from app.api.calls import _build_and_store_memory
        from app.utils.memory_bubble import contact_key_from_call_id
        contacts = sorted({contact_key_from_call_id(cid) for cid, _, _ in results})
        print(f"\nRebuilding memory for {len(contacts)} contacts...")
        mdb = SessionLocal()
        try:
            for ck in contacts:
                try:
                    _build_and_store_memory(ck, mdb)
                    print(f"  memory ok: {ck}")
                except Exception as e:  # noqa: BLE001
                    print(f"  memory skipped {ck}: {str(e)[:80]}")
        finally:
            mdb.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--memory", action="store_true")
    args = ap.parse_args()
    reprocess_all(workers=args.workers, memory=args.memory)
