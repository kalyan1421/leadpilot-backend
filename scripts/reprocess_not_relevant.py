"""
Re-run AI lead-analysis for ONLY the calls currently marked `not_relevant`.

Why this exists: those rows were analysed under the OLD relevance filter, which
forced every dimension to 0 and the verdict to Junk. The analyzer now scores
every dimension on its own merits even for off-topic calls (is_relevant is kept
as a LABEL only), so those stored zeros need refreshing. This backfills them.

Unlike scripts/reprocess_all.py, this script:
  * selects only status == 'not_relevant' rows (safe, targeted),
  * passes each call's real ORGANISATION CONTEXT so relevance is still judged,
  * writes relevance_reason, and
  * sets status = 'completed'/'not_relevant' exactly like the /analyze endpoint
    (run_lead_analysis), so a call that's genuinely still off-topic keeps its
    label — but now with real per-dimension scores.

Usage:
  python scripts/reprocess_not_relevant.py [--workers N] [--dry-run]
    --workers N   number of calls analysed concurrently   (default 5)
    --dry-run     list the calls that WOULD be reprocessed, change nothing
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
from app.api.calls import _org_context
from app.utils.lead_analyzer import analyze_call

# Mirrors the fields run_lead_analysis persists (calls.py) — INCLUDING
# relevance_reason, which reprocess_all.py omits.
_FIELDS = ("bant_score", "bant_breakdown", "lead_verdict", "lead_verdict_reason",
           "relevance_reason", "sentiment_arc", "intent_tags", "entities",
           "call_summary", "key_points", "next_steps", "next_action", "agent_debrief")


def _one(call_id: str):
    """Analyse a single call in its OWN DB session (sessions aren't thread-safe)."""
    db = SessionLocal()
    try:
        call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
        turns = call.transcript.get("turns") if (call and isinstance(call.transcript, dict)) else None
        if not turns:
            return (call_id, "skip", "no transcript")
        # Pass the call's org context so the relevance filter still applies —
        # without it the analyzer always sets is_relevant=True (see the prompt),
        # which would silently strip the not_relevant label off every call.
        org_context = _org_context(db, call.org_id)
        a = analyze_call(call.transcript, org_context)
        if not a:
            return (call_id, "fail", "analysis returned None")
        rec = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
        if not rec:
            rec = LeadAnalysis(id=str(uuid.uuid4()), call_id=call_id, org_id=call.org_id)
            db.add(rec)
        for k in _FIELDS:
            setattr(rec, k, a.get(k))
        # Same terminal-status rule as run_lead_analysis / _process_uploaded_recording.
        rec.status = "completed" if a.get("is_relevant", True) else "not_relevant"
        rec.error = None
        db.commit()
        agent = (a.get("agent_debrief") or {}).get("total_score")
        return (call_id, "ok",
                f"{rec.status:12} verdict={a.get('lead_verdict')}  bant={a.get('bant_score')}  agent={agent}")
    except Exception as e:  # noqa: BLE001
        return (call_id, "fail", str(e)[:120])
    finally:
        db.close()


def reprocess_not_relevant(workers: int = 5, dry_run: bool = False):
    db = SessionLocal()
    try:
        ids = [
            r.call_id
            for r in db.query(LeadAnalysis.call_id)
            .filter(LeadAnalysis.status == "not_relevant")
            .all()
        ]
    finally:
        db.close()

    if not ids:
        print("No calls with status='not_relevant' — nothing to reprocess.")
        return

    if dry_run:
        print(f"[dry-run] {len(ids)} not_relevant calls would be reprocessed:")
        for cid in ids:
            print(f"  {cid}")
        return

    print(f"Reprocessing {len(ids)} not_relevant calls with {workers} parallel workers...")
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
    still_nr = sum(1 for _, s, info in results if s == "ok" and info.startswith("not_relevant"))
    print(f"\nDONE. ok={ok} (of which {still_nr} still labelled not_relevant, now scored) "
          f"fail={fail} skip={skip} of {len(ids)}")
    for cid, s, info in results:
        if s == "fail":
            print(f"  FAILED {cid}: {info}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    reprocess_not_relevant(workers=args.workers, dry_run=args.dry_run)
