# LeadPilot — Backend Integration Guide (single source of truth)

**Audience:** the backend team integrating the AI layer. **Status:** AI layer built, tested, running locally; reasoning provider is config-switchable (Sarvam ↔ Gemini).
**Last updated:** 2026-07-01. This file supersedes the older AI_HANDOVER / BACKEND_MODULES / HANDOVER_NOTE notes.

> One rule above all: **everywhere you see `contact_key`, use the real `lead.phone`.** We slug it from the
> call_id today only because the test data has no phone field. Wire real phones → memory, inbox, dedup all become correct.

---

## 1. What this is (30-second model)

A telecaller calls a lead → the call is **recorded → transcribed + speaker-diarized → AI-scored → remembered**.
The app reads it back. You build CRUD/portals around our endpoints; **you build no AI.**

```
recording ──► Sarvam STT + diarization ──► structured AI analysis ──────► memory bubble
   (upload)        (Saaras v3, batch)     (sarvam-105b OR gemini, tool-calling)   (per phone)
                          │                          │                          │
                          └──────────► persisted in Postgres ◄─────────────────┘
                                              │
            app polls /processing-status, then reads /score, /lead-analysis, /inbox, /memory
```

- **STT + diarization: Sarvam** (Saaras v3) — Indian-language SOTA; **diarization is Sarvam-only**, so this stage never changes. 3 keys, auto-rotated on credit/limit.
- **Reasoning / analysis: config-switchable** via `REASONING_PROVIDER` — `sarvam` (default, `sarvam-105b`, 3-key rotation) **or** `gemini` (`gemini-3.5-flash` recommended / `gemini-3.1-pro-preview`). Same schema + output contract either way; Gemini uses native JSON-schema output, Sarvam uses forced tool-calling. Provider layer: `app/utils/gemini.py` (drop-in for `sarvam_extract`). Model/cost/language rationale: **`docs/AI_ENGINE_DECISION.md`**.
- **Analysis = schema-locked JSON** every time (no parse failures), decomposed + map-reduce for long calls.
- **Deterministic aggregation** (composite Call Score, rings, trends) is plain Python over the LLM signals.

---

## 2. Run it locally (5 steps)

```bash
# Prereqs: Python 3.11+, Node 18+, PostgreSQL 14+, ffmpeg
python -m venv .venv && .venv/Scripts/activate           # mac/linux: source .venv/bin/activate
pip install -r requirements.txt                          # matplotlib included
# create DB:  createdb voicesummary   (or: CREATE DATABASE voicesummary; in psql)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # API docs: http://localhost:8000/docs
cd frontend && npm install && npm run dev                # portal: http://localhost:3000/portal
```
Tables auto-create on startup (`Base.metadata.create_all`). Frontend proxies `/api/*` → `:8000` (see `frontend/next.config.js`).

## 3. Environment (`.env`)

Copy **`.env.example`** → `.env` and fill in real values locally. **Never commit real keys** — the Gemini key is a *billing* key (real money) and secrets in git history are permanent; share them via a password manager, not the repo.

| Var | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@localhost:5432/voicesummary` |
| `SARVAM_API_KEYS` | comma-separated keys; **rotated** when one runs out of credits/rate-limit (STT + default analysis) |
| `SARVAM_CHAT_MODEL` | `sarvam-105b` (analysis) · `sarvam-30b` (cheap worker) |
| `SARVAM_STT_MODEL` / `SARVAM_STT_MODE` | `saaras:v3` / `transcribe` (or `translate`→English) |
| `REASONING_PROVIDER` | which LLM does analysis/scoring: `sarvam` (default) or `gemini`. STT always stays Sarvam. |
| `GEMINI_API_KEYS` | comma-separated Gemini keys (rotated on 429/503) — needed only when `REASONING_PROVIDER=gemini` |
| `GEMINI_MODEL` | `gemini-3.5-flash` (recommended) · `gemini-3.1-pro-preview` |
| `GEMINI_THINKING_LEVEL` | `low` (default) · `medium` · `high` — Gemini 3 thinking budget |
| `STORAGE_MODE` / `LOCAL_STORAGE_PATH` | `local` for dev (audio on disk); `s3` on AWS |

---

## 4. The AI pipeline (automatic — you just trigger + poll)

`POST /api/calls/upload` (multipart) returns **202** immediately and runs a durable background job:
**transcribe → analyse → memory rebuild**. The app polls `/processing-status` for the 4-step stepper, then reads `/score`.

- **Durable:** every job is persisted in the `processing_jobs` table; a crash/restart **re-dispatches** stuck jobs on startup (no lost calls). *On AWS, replace the in-process worker with SQS/Celery consuming this same table — no other code changes.*
- **Empty/failed transcription never fake-completes** — the stepper shows `transcribe: failed`.

---

## 5. API reference (everything you consume)

Base URL `http://localhost:8000`. All live now — try them at `/docs`.

### 5.1 Score tab — the one call that renders the whole screen
`GET /api/calls/{call_id}/score` → **the frontend renders this verbatim; it computes nothing.**
```jsonc
{
  "call_id": "...",
  "call_score": 70,                 // hero ring = 0.45·overall + 0.30·lead_quality + 0.25·sentiment
  "rings": {                        // trend = delta vs THIS contact's previous call (null → show "—")
    "overall":      {"value":71,"max":100,"trend":5},    // agent_debrief.total_score (this call)
    "telecaller":   {"value":84,"max":100,"trend":2},    // rolling agent avg (NOT this call)
    "lead_quality": {"value":76,"max":100,"trend":-3},   // bant_score
    "sentiment":    {"value":63,"max":100,"trend":null}  // derived from sentiment_arc
  },
  "verdict": "Warm",
  "transcript_quality": "ok",       // ok | low | failed — caveat the UI when not ok
  "breakdown": [                    // 5 dims: score + note + AUDITABLE evidence + trust status
    {"key":"opening","label":"Opening","score":16,"max":20,
     "note":"Warm greeting, used name early.",
     "evidence":[{"turn":1,"t":"0:01","speaker":"AGENT","text":"Hello Mr Kumar, Ramya from Prestige."}],
     "status":"beta"}              // validated | beta | hidden  (gold-set gate; hidden rows omitted)
  ],
  "strengths":[...], "improvements":[...],
  "sentiment_timeline": {"segments":[{"index":0,"t0":"0:00","t0_sec":0,"t1_sec":78,"label":"neutral","avg_score":0.05}],
                         "caption":"Prospect warmed up around 3:54. No negative spike detected."}
}
```
`segments[].label` ∈ `neutral|cautious|interested|frustrated`. **Always show the `evidence` quote next to each score** (auditable; turns "trust me" into proof).

### 5.2 Per-call analysis
| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/calls/{id}/lead-analysis` | run/re-run full analysis (BANT, sentiment_arc, intent, key_points, next_steps, agent_debrief w/ notes+evidence) |
| `GET` | `/api/calls/{id}/lead-analysis` | fetch stored analysis (raw fields) |
| `GET` | `/api/calls/{id}/processing-status` | 4-step stepper (Upload→Transcribe→Analyse→Done) |

### 5.3 Calls & transcript
| `GET/POST` | `/api/calls/` , `/api/calls/{id}` | list / detail / create |
| `GET` | `/api/calls/{id}/audio` | stream audio |
| `GET` | `/api/calls/{id}/transcript` | diarized turns `[{role,content,timestamp}]` |
| `GET` | `/api/calls/{id}/transcript/translate?target=en` | "View English" toggle |
| `POST` | `/api/calls/upload` | multipart (`file`,`name?`,`phone?`,`source?`) → 202 + durable pipeline |

### 5.4 Inbox / memory / telecaller (cross-call intelligence)
| `GET` | `/api/inbox?bucket=high_intent\|new\|follow_up\|cold` | inbox cards + header stats |
| `GET` | `/api/memory/{contact_key}` | cumulative memory bubble (facts w/ Call #N) |
| `POST` | `/api/memory/{contact_key}/rebuild` | force rebuild |
| `GET` | `/api/leads/{contact_key}` | lead detail aggregate (card + memory + call history) |
| `POST` | `/api/leads` · `GET /api/leads/dedupe?phone=` | create lead / duplicate check |
| `GET` | `/api/telecaller/score?window_days=7` | rolling agent score + trend (^N) |

*(Separate, optional feature: `/api/comparisons/*` — AI voice-agent comparison/testing. Independent of the telecaller pipeline.)*

---

## 6. Contract rules you must honour
1. **`contact_key` → real `lead.phone`.** The slug is a stand-in; swap it and memory/inbox/dedup become correct.
2. **Always show `evidence`** under each score (auditable quote). A score with no evidence → hide it.
3. **Respect `status`** per dimension: `beta` = show with a beta tag; `hidden` = don't render. Promote to `validated` only via the gold-set gate (§8).
4. **Surface `transcript_quality`** — caveat scores when `low`/`failed`.
5. **Governance (mandatory, see GOVERNANCE.md):** scores are a *coaching aid, never the sole basis for pay/firing/ranking* (human-in-the-loop + appeal); record/analyse only **with consent (DPDP)**; build an **audit log**.

---

## 7. Data model (tables this layer owns)
- `audio_calls` — call_id, transcript(JSON: turns+language+quality), audio_file_url, timestamp
- `lead_analysis` — per-call AI output (bant_score, bant_breakdown, lead_verdict, sentiment_arc, intent_tags, entities, call_summary, key_points, next_steps, next_action, **agent_debrief** {5×score+note+evidence}, status)
- `memory_bubbles` — per-contact cumulative memory
- `leads` — thin lead store (contact_key, name, phone, source, status)
- `processing_jobs` — **durable pipeline state** (call_id, stage, status, attempts) for crash-recovery

## 8. Validation gate (before trusting scores)
`config/score_dimensions.json` controls per-dimension status. A dimension stays **beta** until it clears the bar:
```bash
python scripts/gold_set_eval.py --template     # make gold_set.json, fill 30-50 calls with human 0-20 scores
python scripts/gold_set_eval.py --write         # measures per-dimension MAE/correlation → promotes passers to "validated"
```
Re-run after any model/prompt change (drift). `/score` reads the status; the UI tags non-validated dims.

## 9. What's DONE vs what you BUILD
**Done (call it):** Modules 3 (calls/recordings), 4 (analysis/scoring + `/score`), 5 (memory), translation, processing stepper, durable pipeline, thin leads + dedupe.
**You build:** **M1 Org Knowledge Base** (grounds AI relevance/scoring), **M2 full Lead mgmt** (org_id, assignment, bulk, ad webhooks, **real phone keys**), **M6 Follow-up/Messaging** (turn `next_steps` into WhatsApp/SMS sends), **M7/M8 Analytics + AI chat**. Add `agent_id` to calls so telecaller score filters per agent.

## 10. Going to AWS (later — built to port cleanly)
- `processing_jobs` table → swap the in-process recovery for an **SQS/Celery worker** consuming the same table.
- `STORAGE_MODE=s3` + real bucket (audio).
- **Buy a paid Sarvam tier** (rate limits + higher `max_tokens`); drop the free-key rotation to a single keyed account with retry/backoff.
- Add the **indexed `contact_id`/phone column** + replace the current full-scan grouping queries (deferred — fine at current volume, required at scale).
- Move config/secrets to AWS Secrets Manager; put the app behind a load balancer; managed Postgres (RDS).

---
*Deeper references in-repo:* `GOVERNANCE.md` (trust/legal guardrails), `docs/AI_ENGINE_DECISION.md` (model/provider choice — reasoning ranks, Indian-language fit, live ₹ costs & forex), `gold_set_eval.py` (validation), `app/utils/sarvam.py` (Sarvam provider + rotation + STT), `app/utils/gemini.py` (Gemini provider — drop-in for the analysis step), `app/utils/lead_analyzer.py` (the analysis engine + provider switch).
