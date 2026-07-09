# 🎯 LeadPilot

**AI call intelligence for Indian telecalling teams.** Record a sales call → it's transcribed,
speaker-diarized, scored, and remembered — so every rep gets coaching and every lead gets a verdict.

<p>
<code>Python 3.11 · FastAPI</code> &nbsp;·&nbsp; <code>Next.js 14 · TypeScript</code> &nbsp;·&nbsp;
<code>PostgreSQL</code> &nbsp;·&nbsp; <code>Sarvam AI (STT + LLM)</code>
</p>

---

## What it does

```
  📞 recording ──▶ Sarvam STT + diarization ──▶ structured AI analysis ──▶ memory bubble
                      (Saaras v3, batch)          (sarvam-105b, tool-calling)    (per phone)
                                         │                                  │
                                         ▼                                  ▼
                                   PostgreSQL  ◀──── deterministic scoring (rings, trends)
                                         │
                          Telecaller portal: Call Score · Summary · Transcript
```

Every recorded call produces:
- a **Call Score** + four rings — Overall (rep execution), Telecaller (rolling), Lead Quality (BANT), Sentiment;
- a **5-dimension breakdown** (Opening / Discovery / Pitch / Objection / Closing) — each with a one-line note **and an auditable transcript quote**;
- a **sentiment timeline**, **key points**, and **next steps**;
- a per-contact **memory bubble** that makes the next call smarter.

## Features

| | |
|---|---|
| 🎙️ **Diarized transcription** | Sarvam Saaras v3 — SOTA for Indian languages (Hindi/Telugu/…); speakers split into a chat |
| 🧠 **Structured AI analysis** | sarvam-105b via **forced tool-calling** → guaranteed-valid JSON; decomposed + map-reduce for long calls |
| 🔍 **Evidence-backed scores** | every score cites the exact quote + timestamp — auditable, not "trust me" |
| 🫧 **Memory bubble** | cumulative per-contact facts, objections, commitments across all calls |
| 🌐 **Multilingual** | Indian-language native; one-tap "View English" |
| ♻️ **Durable pipeline** | crash-safe job queue (`processing_jobs`) with startup recovery |
| 🛡️ **Trust & governance** | gold-set validation gate + consent / "coaching aid, not a verdict" guardrails |

## Quickstart

**Prerequisites:** Python 3.11+, Node 18+, PostgreSQL 14+.

```bash
# 1. Backend
python -m venv .venv && .venv/Scripts/activate     # mac/linux: source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env                                # set DATABASE_URL + SARVAM_API_KEYS
createdb voicesummary
python -m uvicorn app.main:app --port 8000 --reload   # API docs → http://localhost:8000/docs

# 2. Frontend
cd frontend && npm install && npm run dev          # portal → http://localhost:3000/portal
```
The only hard requirement is **PostgreSQL + a Sarvam API key** (https://dashboard.sarvam.ai). Tables auto-create on startup.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```
Tests run against an isolated in-memory SQLite DB (see `tests/conftest.py`) — never the
configured `DATABASE_URL` — so they're safe to run against a dev database with real data.

## Project structure

```
app/
  main.py            # FastAPI app + startup (table create + pipeline recovery)
  config.py          # settings (env-driven)
  database.py        # SQLAlchemy engine/session
  models.py          # AudioCall · LeadAnalysis · MemoryBubble · Lead · ProcessingJob
  schemas.py         # Pydantic response models
  api/calls.py       # all endpoints (calls, /score, lead-analysis, inbox, memory, leads, upload, telecaller)
  utils/
    sarvam.py        # ⭐ sole AI provider: 3-key rotation, STT+diarization, tool-calling, chat
    lead_analyzer.py # structured analysis (decomposed + map-reduce + validation)
    lead_intelligence.py  # deterministic scoring (rings, composite, trends, sentiment)
    memory_bubble.py · transcription.py · translation.py · local_storage.py · s3.py
config/score_dimensions.json   # per-dimension trust status (validated/beta/hidden)
scripts/             # ops/dev utilities (gold_set_eval, import_audio, reprocess_all, …)
frontend/            # Next.js telecaller portal (app/portal, components/portal)
```

## API

All endpoints + exact JSON shapes are in **[BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md)** — the single
integration guide for the backend team. The headline one:

```
GET /api/calls/{id}/score   → hero Call Score + 4 rings (w/ trends) + breakdown (w/ notes + evidence) + sentiment timeline
```
Interactive docs at `http://localhost:8000/docs`.

## AI provider

**Sarvam AI only** (Indian-language SOTA + data residency). Three API keys are rotated automatically on
credit/rate limits. Models: `saaras:v3` (STT+diarization), `sarvam-105b` (analysis), `mayura:v1` (translation).
Swapping the reasoning model is a config change — see [BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md).

## Trust, cost & deployment

- **Governance (read before going live):** [GOVERNANCE.md](GOVERNANCE.md) — DPDP consent, no automated adverse
  decisions, evidence requirement, gold-set gate. Validate scores with `python scripts/gold_set_eval.py`.
- **Cost:** [COST_ANALYSIS.md](COST_ANALYSIS.md) — STT dominates (~₹45/hr of audio); LLM is a rounding error.
- **AWS:** swap the in-process recovery for an SQS/Celery worker on `processing_jobs`; `STORAGE_MODE=s3`;
  paid Sarvam tier; managed Postgres. The code is built to port without changes.

## License

See [LICENSE](LICENSE).
