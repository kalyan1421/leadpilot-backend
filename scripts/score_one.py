"""
Score / inspect ONE call — so a developer can check the scores (and the exact
system prompt that drives them) against a real recording.

The base that makes every score behave the same way is the system prompt
`_SCORING_SYS` in app/utils/lead_analyzer.py. This tool lets you see it and
run it on any call.

Usage:
  python scripts/score_one.py <call_id>            # show STORED breakdown (free, instant)
  python scripts/score_one.py <call_id> --rerun    # RE-RUN the model on this call (calls Sarvam)
  python scripts/score_one.py --text "Turn 1 [AGENT]: hello\nTurn 2 [USER]: tell me the price"
  python scripts/score_one.py <call_id> --prompt   # also print the system prompt (the base)

Notes:
  - default reads the saved analysis (no API cost) — great for checking scores vs the audio.
  - --rerun / --text call the live Sarvam model (uses an API key) — use to test prompt changes.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # so Telugu/Hindi quotes print on Windows
except Exception:
    pass

import argparse
import logging
import re

import app.database  # noqa: F401 — import first so the DB engine exists...
try:
    app.database.engine.echo = False  # ...then turn off SQL echo so the report is clean
except Exception:
    pass
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.utils.lead_analyzer import analyze_call, _SCORING_SYS, _SENTIMENT_SYS, _DIGEST_SYS

_DIMS = [("opening", "Opening"), ("discovery", "Discovery"), ("pitch", "Pitch"),
         ("objection_handling", "Objection Handling"), ("closing", "Closing")]


def _stored(call_id):
    from app.database import SessionLocal
    from app.models import LeadAnalysis
    db = SessionLocal()
    try:
        la = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == call_id).first()
        if not la:
            sys.exit(f"No stored analysis for {call_id}. Add --rerun to score it now.")
        return {
            "lead_verdict": la.lead_verdict, "lead_verdict_reason": la.lead_verdict_reason or "",
            "call_summary": la.call_summary or {}, "bant_breakdown": la.bant_breakdown or {},
            "bant_score": la.bant_score or 0, "agent_debrief": la.agent_debrief or {},
        }
    finally:
        db.close()


def _transcript(call_id):
    from app.database import SessionLocal
    from app.models import AudioCall
    db = SessionLocal()
    try:
        c = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
        if not c:
            sys.exit(f"Call {call_id} not found")
        return c.transcript or {}
    finally:
        db.close()


def _text_to_transcript(text):
    turns = []
    for line in text.replace("\\n", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"(?:Turn\s*\d+\s*)?\[?(AGENT|USER)\]?\s*[:\-]\s*(.+)", line, re.I)
        if m:
            turns.append({"role": m.group(1).upper(), "content": m.group(2), "timestamp": "0:00"})
        else:
            turns.append({"role": "USER", "content": line, "timestamp": "0:00"})
    return {"turns": turns}


def _print(r):
    d = r.get("agent_debrief") or {}
    cs = r.get("call_summary") or {}
    print("=" * 72)
    print(f"VERDICT : {r.get('lead_verdict')}   ({r.get('lead_verdict_reason') or '-'})")
    print(f"HEADLINE: {cs.get('headline') or '-'}")
    print("-" * 72)
    print(f"LEAD QUALITY  (BANT)      = {r.get('bant_score') or 0}/100")
    for k, v in (r.get("bant_breakdown") or {}).items():
        v = v or {}
        print(f"   {k:<10} {str(v.get('score', 0)):>2}/25   {v.get('reason') or ''}")
    print("-" * 72)
    print(f"REP EXECUTION (Overall)   = {d.get('total_score') or 0}/100")
    for key, label in _DIMS:
        print(f"   {label:<18} {str(d.get(key + '_score', 0)):>2}/20   {d.get(key + '_note') or ''}")
        for e in (d.get(key + "_evidence") or []):
            print(f"        > [{e.get('t')}] {e.get('speaker')}: {e.get('text')}")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(description="Score / inspect one call.")
    ap.add_argument("call_id", nargs="?", help="stored call_id")
    ap.add_argument("--text", help="ad-hoc transcript text instead of a call_id")
    ap.add_argument("--rerun", action="store_true", help="re-run the model (calls Sarvam)")
    ap.add_argument("--prompt", action="store_true", help="print the system prompts (the base)")
    args = ap.parse_args()

    if args.prompt:
        for title, p in [("SCORING (drives all scores)", _SCORING_SYS),
                         ("SENTIMENT", _SENTIMENT_SYS), ("DIGEST (long calls)", _DIGEST_SYS)]:
            print("=" * 72)
            print(f"SYSTEM PROMPT — {title}")
            print("=" * 72)
            print(p, "\n")

    if args.text:
        print("[scoring ad-hoc transcript via the live model...]")
        r = analyze_call(_text_to_transcript(args.text))
    elif args.call_id and args.rerun:
        print(f"[re-running the model on {args.call_id}...]")
        r = analyze_call(_transcript(args.call_id))
    elif args.call_id:
        r = _stored(args.call_id)
    else:
        ap.error("give a <call_id> or --text")
        return

    if not r:
        sys.exit("Analysis failed (see logs).")
    _print(r)


if __name__ == "__main__":
    main()
