"""
Gold-set evaluation harness — turns "trust" into a MEASURABLE, per-dimension gate.

Council requirement #4: a score dimension is not trustworthy until it clears a bar vs
HUMAN raters. This compares the AI's per-dimension scores to a human-labeled gold set,
computes per-dimension MAE + correlation, and (with --write) promotes dimensions that
pass to "validated" in config/score_dimensions.json (what the /score endpoint gates on).

Usage:
  python gold_set_eval.py --template   # write a gold_set.json template to fill in
  python gold_set_eval.py               # evaluate vs gold_set.json, print report (dry run)
  python gold_set_eval.py --write       # also update config/score_dimensions.json

gold_set.json format (use 30-50 calls; ideally 2 human raters per call, averaged):
[ {"call_id": "call_xxx", "labels": {"opening":16,"discovery":14,"pitch":15,
                                      "objection_handling":12,"closing":10}}, ... ]

Acceptance bar (tune below): MAE <= 3.0 on the 0-20 scale AND >= 8 labeled calls.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import os
import statistics
import sys

DIMS = ["opening", "discovery", "pitch", "objection_handling", "closing"]
GOLD = "gold_set.json"
STATUS = os.path.join("config", "score_dimensions.json")
MAE_BAR = 3.0
MIN_N = 8


def _ai_scores(call_id):
    """Per-dimension AI scores for a call — prefers stored analysis, else runs it."""
    from app.database import SessionLocal
    from app.models import LeadAnalysis, AudioCall
    from app.utils.lead_analyzer import analyze_call
    db = SessionLocal()
    try:
        la = db.query(LeadAnalysis).filter(
            LeadAnalysis.call_id == call_id, LeadAnalysis.status == "completed").first()
        deb = la.agent_debrief if la else None
        if not deb:
            call = db.query(AudioCall).filter(AudioCall.call_id == call_id).first()
            if not call:
                return None
            a = analyze_call(call.transcript or {"turns": []})
            deb = a.get("agent_debrief") if a else None
        return {d: (deb or {}).get(f"{d}_score") for d in DIMS} if deb else None
    finally:
        db.close()


def template():
    json.dump([{"call_id": "<paste a real call_id>", "labels": {d: 0 for d in DIMS}}],
              open(GOLD, "w", encoding="utf-8"), indent=2)
    print(f"Wrote {GOLD} template — fill 30-50 calls with human 0-20 scores per dimension, then re-run.")


def main():
    if "--template" in sys.argv:
        template(); return
    if not os.path.exists(GOLD):
        print(f"No {GOLD}. Run:  python gold_set_eval.py --template"); return

    gold = json.load(open(GOLD, encoding="utf-8"))
    pairs = {d: [] for d in DIMS}
    used = 0
    for row in gold:
        cid, labels = row.get("call_id"), (row.get("labels") or {})
        if not cid or cid.startswith("<"):
            continue
        ai = _ai_scores(cid)
        if not ai:
            print(f"  skip {cid}: no AI scores"); continue
        used += 1
        for d in DIMS:
            h, a = labels.get(d), ai.get(d)
            if isinstance(h, (int, float)) and isinstance(a, (int, float)):
                pairs[d].append((float(h), float(a)))

    print(f"\nGold-set eval — {used} call(s)\n" + "=" * 60)
    result = {}
    for d in DIMS:
        ps = pairs[d]; n = len(ps)
        if n == 0:
            print(f"{d:22} no data"); result[d] = "beta"; continue
        mae = sum(abs(h - a) for h, a in ps) / n
        try:
            hs, as_ = [h for h, _ in ps], [a for _, a in ps]
            corr = statistics.correlation(hs, as_) if n >= 2 and len(set(hs)) > 1 and len(set(as_)) > 1 else float("nan")
        except Exception:
            corr = float("nan")
        passed = mae <= MAE_BAR and n >= MIN_N
        result[d] = "validated" if passed else "beta"
        tag = "VALIDATED ✓" if passed else f"beta (need MAE<={MAE_BAR:.0f}, n>={MIN_N})"
        print(f"{d:22} n={n:3}  MAE={mae:4.1f}  corr={corr:+.2f}  ->  {tag}")

    if "--write" in sys.argv:
        json.dump({"_comment": "auto-updated by gold_set_eval.py", **result},
                  open(STATUS, "w", encoding="utf-8"), indent=2)
        print(f"\nWrote {STATUS}: {result}")
    else:
        print("\n(dry run — re-run with --write to update config/score_dimensions.json)")


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    main()
