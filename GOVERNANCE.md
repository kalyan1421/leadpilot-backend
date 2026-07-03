# LeadPilot — AI Governance & Trust Guardrails

Scoring humans on AI output is a regulated, high-trust activity. These guardrails are **mandatory**,
independent of model quality, and were ratified by the LLM Council review (2026-06-29). They address
the risks that a better reasoning model does **not** fix.

## 1. No automated adverse decisions (hard rule)
A call score is a **coaching aid, never the sole basis** for pay, ranking, promotion, or disciplinary
action. Any such decision requires a **human-in-the-loop** review of the actual recording and a
documented rationale. The UI states this on every Score view.
- **Build requirement:** an **appeal path** — a telecaller can dispute any score; disputes are logged and reviewed.

## 2. Consent (DPDP Act, India)
Calls may only be recorded and AI-analysed with **consent** (IVR consent message per language for inbound/outbound).
Store consent state per call; do not analyse calls lacking consent. Provide data-deletion on request.

## 3. Evidence, not bare numbers
Every score dimension ships with the **supporting transcript quote + timestamp** (`evidence` in `/score`).
A score with no citable evidence is unfalsifiable and must be hidden/flagged, not displayed.

## 4. Trust is earned per dimension (validation gate)
Do not present a dimension as trustworthy until it clears the **gold-set acceptance bar**
(`python scripts/gold_set_eval.py` — per-dimension agreement vs human raters). Dimensions below the bar are
shown as **beta** or hidden (`SCORE_DIMENSION_STATUS`). Re-validate after any model/prompt change (drift).

## 5. Transcription error is inherited, and non-uniform
STT is ~19% WER on Indian languages (SOTA, but real). Error is **higher** on code-mixed, accented,
rural-dialect, low-SNR calls — which correlate with specific reps/regions. Therefore:
- Surface `transcript_quality`; flag low-confidence calls; do not score empty transcripts.
- **Do not rank reps against each other** until WER is measured per segment and shown to be comparable —
  otherwise scores are systematically biased, a fairness/labour-law risk.

## 6. Goodhart / gaming
Once the rubric is known, reps optimise to trigger it rather than sell better. Monitor for keyword-stuffing
and score inflation without conversion improvement; treat the rubric as evolving, not fixed.

## 7. Audit log (build requirement)
Log who viewed/acted on a score and when, plus every analysis run (model, version, timestamp), so any
adverse decision is traceable and contestable.

---
*Sequencing:* Items 1, 2 (consent + no-auto-decision) are **non-negotiable from day one** — irreversible
liability. Items 4, 7 (gold-set gate, audit log) are required **before scaling** beyond a design-partner pilot.
