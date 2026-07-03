# LLM Council Transcript — LeadPilot AI Stack Review
**Date:** 2026-06-29 · **Mode:** Full Council (5 advisors + anonymized peer review + Devil's Advocate + chairman)

## Raw question (user)
"Is all of this — code, techniques, API strategy, product, features, outputs — industry best-practice as of 2026? Use sarvam-30b or 105b? Can the business trust Sarvam analysis or not?"

## Framed question
Evaluate the LeadPilot telecaller AI stack for production readiness and 2026 best-practice. Stack: Python/FastAPI + Next.js + Postgres; **Sarvam-only** pipeline — Saaras v3 diarized batch STT → structured analysis via forced tool-calling (decomposed into scoring + per-turn sentiment, map-reduce chunking, reasoning_effort='low', clamping/retry) → translation + memory; deterministic score aggregation in Python; 3 rotating free starter keys. Decide: (a) 30b vs 105b; (b) can the business trust Sarvam's analysis enough to show scores to telecallers/founders and decide on them; (c) best-practice gaps. **Verified research:** Sarvam LLM 105B=18 / 30B=12 on Artificial Analysis Intelligence Index (frontier ~50-70; weak general reasoning) but 105B dominates Indian-language tasks (self-reported); Saaras v3 STT 19.3% WER on Indic = SOTA for Indian languages, slightly behind on pure English, diarization included. Live test: full pipeline works on a real Telugu call.

## Anonymization mapping (peer review)
A = Expansionist · B = Outsider · C = Contrarian · D = Executor · E = First Principles

## Advisor responses
**Contrarian:** 18-index LLM is the fatal flaw for the *scoring* (reasoning) step; forced tool-calling yields valid-but-wrong JSON. 105B > 30B unconditionally, but both weak judges. Don't trust yet — pipeline RUNS ≠ scores RIGHT. Build a human-labeled gold set (50-100 calls, 2 raters), measure per-dimension correlation/MAE, ship only dimensions that clear a bar.

**First Principles:** Solving the wrong problem — false precision vs "one better decision/day." Trust transcription/sentiment-direction/summaries, not fine-grained numeric scores. Collapse 0-20 into bands + supporting quote; then 30b-vs-105b is moot; spend 105B on summaries/memory.

**Expansionist:** The 10x is the keyword→ad loop as a sovereign Indian-language intent graph. 105B everywhere. Present scores as deterministic aggregation over schema-locked extractions. Vernacular transcripts = a dataset Google/Meta can't see; resell as QA/coaching/vertical SLM; sovereignty = BFSI/gov moat.

**Outsider:** Scores look precise but rest on 1-in-5 word errors + an arbitrary even 20×5 rubric; reps can't learn from a number with no rule shown; bosses judging jobs on it = ranking humans on a guess dressed as math. Show the quote, admit uncertainty.

**Executor:** Not production-ready. BackgroundTasks isn't a durable queue (drops in-flight calls); free-key/4096/rate-limit risk; full-scan DB dies at 50k; batch STT + per-turn LLM = unbounded latency. Order: durable queue → paid tier+backoff → contact_id index → cap per-turn sentiment. Keep 105B.

## Peer review tallies
- **Strongest: Contrarian (C) — 5/5 unanimous.** Only advisor making trust *measurable* (gold set + per-dimension MAE acceptance gate).
- **Most-flagged blind spots:** Contrarian ignores production reliability (Executor's queue/DB/rate-limit risks) — 3 reviewers; Expansionist monetizes unvalidated output — 1 reviewer.
- **Universal misses (all advisors):** (1) employment-law + DPDP consent for ranking humans on AI [4 reviewers]; (2) non-uniform WER → systematic bias vs specific reps/regions; (3) STT→LLM error compounding + unmeasured real WER; (4) Goodhart gaming of a known rubric; (5) post-launch drift + recurring eval cost; (6) gold-set rater reliability.

## Devil's Advocate
Attacked the gold-set/queue/de-precision bar as pilot-stage over-engineering (Gong/Chorus ship directional scores with quotes, no public gold set; build the gold set from production; closed-domain QA ≠ open reasoning so 18/100 is less damning; BackgroundTasks survives dozens of calls/day). **Conceded** the irreversible items: DPDP/labor-law consent + "never fire/pay on the score" guardrail + Goodhart gaming. Net: stage the recommendation by maturity.

## Chairman verdict
**Confidence: MEDIUM–HIGH.** Near-unanimous on the diagnosis (don't treat scores as truth; 105B; trust Sarvam for Indic STT/language not fine-grained reasoning); the one genuine clash (validation/infra depth before launch) was productively narrowed by the Devil's Advocate into a maturity-staged answer.

- **Techniques/code = genuinely 2026 best-practice** (tool-calling structured output, decomposition, map-reduce, deterministic aggregation). Keep.
- **(a) → sarvam-105B**, unconditionally (cost delta negligible).
- **(b) → trust split:** YES for STT/diarization/language/extraction/summaries (Indic SOTA); NOT-as-ground-truth for fine-grained numeric scores — present them evidence-backed (quote attached), directional, as aggregation over schema-locked extractions.
- **Pilot now:** ship directional scores with quotes; build the gold set from production; keep BackgroundTasks.
- **Before scale:** durable queue, paid tier, contact_id index, per-dimension gold-set gate, drift monitoring, cap per-turn sentiment.
- **Non-negotiable now (irreversible):** DPDP consent; "coaching aid, never a pay/firing basis" guardrail (human-in-loop + appeal); watch Goodhart gaming.
- **One thing first:** attach the supporting transcript quote+timestamp to every score dimension this week + a "coaching aid, not a verdict" + consent disclaimer.
- **Tripwires:** reps right >20% when disputing a score → pull that dimension; no quote → don't display; any pay/firing use → stop; WER worse for certain reps/regions → don't rank across reps.

*Grounding sources:* Artificial Analysis Intelligence Index (Sarvam 30B/105B); Saaras v3 IndicVoices WER (Business Standard, Scribie); 2026 structured-output + map-reduce best-practice literature; live end-to-end Telugu-call test in this repo.
