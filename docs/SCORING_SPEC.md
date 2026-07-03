# 📊 LeadPilot — Score Breakdown Specification

**What this answers:** exactly *what prompts/instructions we send to the AI*, *what it is forced to return*, and *how that becomes the "Score Breakdown" you see on the Score tab*. Every block below is copied verbatim from the code.

> **Source files:** `app/utils/lead_analyzer.py` (prompts + schema + assembly) · `app/api/calls.py` (`/score` endpoint) · `config/score_dimensions.json` (trust gate)

---

## 🔁 The chain (transcript → breakdown card)

```
  Diarized transcript
        │  numbered:  "Turn 7 [AGENT]: <text>"
        ▼
  ┌───────────────────────────────────────────────────────┐
  │  ONE forced-tool-call to Sarvam (sarvam-105b)           │
  │   • system prompt  = the rubric/instructions            │
  │   • user message   = the numbered transcript            │
  │   • output         = LOCKED to a JSON schema (no prose)  │
  └───────────────────────────────────────────────────────┘
        │  raw scores: opening_score=14, opening_note=..., opening_evidence_turns=[7,9]
        ▼
  ASSEMBLE  →  clamp each 0–20 · resolve evidence turns → quotes · sum → total
        │
        ▼
  /score endpoint  →  breakdown[] = { key, label, score, max:20, note, evidence, status }
        │
        ▼
  Score tab card:  "Discovery  14/20  •  <note>  •  «quote at 02:35»"
```

**Two breakdowns exist** — both produced by the same single call:
| Breakdown | Dimensions | Each | Sum | Drives |
|---|---|---|---|---|
| **Telecaller execution** (the "Score Breakdown" card) | opening · discovery · pitch · objection_handling · closing | 0–20 | → **Overall ring** (0–100) | rep coaching |
| **BANT** (lead quality) | budget · authority · need · timeline | 0–25 | → **Lead Quality ring** (0–100) | lead value |

---

## 1️⃣ What we SEND — the input

The diarized transcript is flattened into **numbered turns** so the model can cite exact turn numbers as evidence:

```
TRANSCRIPT:
Turn 1 [AGENT]: నమస్తే సర్, ఎన్‌కార్డియో నుండి మాట్లాడుతున్నాను...
Turn 2 [USER]: చెప్పండి
Turn 3 [AGENT]: మీ బడ్జెట్ ఎంత సర్?
...
```

*Builder: `_numbered()` → `Turn {N} [{ROLE}]: {content}` · `_score_messages()`*

---

## 2️⃣ What we INSTRUCT — the system prompt (verbatim)

This is the **exact** rubric/instruction string sent as the system message (`_SCORING_SYS`):

```text
You are an expert sales-call analyst for an Indian telecalling team. Analyse the call
and record the structured result. Rules: BANT dimensions are each 0-25; the 5 telecaller
dimensions (opening/discovery/pitch/objection_handling/closing) are each 0-20; each *_note
is ONE short sentence (max 14 words) explaining that score; verdict: Hot (strong buy intent),
Warm (interested), Cold (weak), Junk (wrong number/irrelevant). Leave an entity blank only if
truly not stated. next_steps: 1-4 concrete actions with a valid action_type.
CRITICAL: for each of the 5 telecaller dimensions, return *_evidence_turns = the 1-2 Turn
NUMBERS from the transcript that most justify that score. Cite the actual turns you judged from.
```

**What this instruction enforces:**
- 🔢 Fixed ranges — telecaller dims **0–20**, BANT dims **0–25**
- ✍️ Each score must carry a **≤14-word note** (the one-liner under the bar)
- 🧾 **Evidence is mandatory** — the model must name the *actual turn numbers* it judged from (anti-hallucination: we resolve those to real quotes, so it can't invent them)
- 🚫 No empty entities unless genuinely not stated

> 📌 **This is THE base.** Every score on every call obeys this one prompt (`_SCORING_SYS`). Change this string → all scores shift. Print it live to confirm what's running:
> ```bash
> python scripts/score_one.py --prompt
> ```

---

## 3️⃣ What we FORCE — the output schema

We don't ask for JSON in prose — the model is **constrained** to fill this schema (forced tool-calling, `tool_name="record_analysis"`). It **cannot** return free text. The breakdown-relevant fields (`_SCORING_SCHEMA`):

```jsonc
{
  // Telecaller execution — each 0-20 + a one-line note
  "opening_score": int,            "opening_note": str,
  "discovery_score": int,          "discovery_note": str,
  "pitch_score": int,              "pitch_note": str,
  "objection_handling_score": int, "objection_handling_note": str,
  "closing_score": int,            "closing_note": str,

  // Evidence — the Turn numbers that justify each score (resolved to quotes downstream)
  "opening_evidence_turns": [int],
  "discovery_evidence_turns": [int],
  "pitch_evidence_turns": [int],
  "objection_handling_evidence_turns": [int],
  "closing_evidence_turns": [int],

  "strengths":    [str],   // bullet wins
  "improvements": [str],   // bullet coaching points

  // BANT — each 0-25 + reason  (lead-quality breakdown)
  "budget_score": int,    "budget_reason": str,
  "authority_score": int, "authority_reason": str,
  "need_score": int,      "need_reason": str,
  "timeline_score": int,  "timeline_reason": str

  // (+ verdict, summary, next_steps, entities — omitted here for brevity)
}
```

**Required** (model must return these): all 5 telecaller scores, all 4 BANT scores, `lead_verdict`, `key_points`, `next_steps`, `overall_tone`.

---

## 4️⃣ The rubric — what each dimension means

### Telecaller execution (0–20 each → Overall /100)
| Dimension | Judges |
|---|---|
| **Opening** | greeting, rapport, agenda-setting |
| **Discovery** | needs-probing, listening quality |
| **Pitch** | product-fit articulation, confidence |
| **Objection Handling** | addressing pushback, resilience |
| **Closing** | driving commitment, clear next steps |

### BANT (0–25 each → Lead Quality /100)
| Dimension | Judges |
|---|---|
| **Budget** | funds / budget for this product category |
| **Authority** | is this person the decision-maker? |
| **Need** | does the stated problem match the product? |
| **Timeline** | urgency of the decision |

---

## 5️⃣ How we ASSEMBLE it (`_assemble`)

The raw model output is sanitized into the stored contract:

1. **Clamp** every score to its range, *safely* — non-numeric or `inf/-inf/nan` → falls back to the floor (prevents a crash on a bad value):
   ```python
   def clamp(v, lo, hi):
       f = float(v)                       # non-numeric → lo
       if not math.isfinite(f): return lo # inf/nan → lo
       return max(lo, min(hi, int(round(f))))
   ```
2. **Total** = sum of the 5 telecaller scores → `agent_debrief.total_score` (0–100, the **Overall ring**).
3. **BANT total** = sum of the 4 BANT scores (0–100, the **Lead Quality ring**).
4. **Resolve evidence** — each cited turn number → an exact quote (`_evidence`), capped at 3 per dimension, dedup'd, out-of-range/`bool` turns dropped:
   ```
   evidence = { turn: 7, t: "02:35", speaker: "USER", text: "<exact transcript line>" }
   ```

---

## 6️⃣ How the `/score` endpoint builds the breakdown card

`GET /api/calls/{id}/score` turns the assembled debrief into the card array (`app/api/calls.py`):

```python
breakdown = [
  {
    "key":      key,                                # "discovery"
    "label":    label,                              # "Discovery"
    "score":    debrief[f"{key}_score"] or 0,       # 14
    "max":      20,
    "note":     debrief[f"{key}_note"] or "",       # the ≤14-word line
    "evidence": debrief[f"{key}_evidence"] or [],   # [{turn,t,speaker,text}]
    "status":   statuses.get(key, "beta"),          # trust gate (below)
  }
  for key, label in DIMS
  if statuses.get(key, "beta") != "hidden"          # hidden dims are dropped
]
```

The Score tab renders each item as: **label + score/20 bar + note + expandable evidence quote.**

---

## 7️⃣ The trust gate — beta / validated / hidden

Every dimension carries a **trust status** from `config/score_dimensions.json`. This is the gold-set gate: a dimension stays `beta` until `scripts/gold_set_eval.py` shows it matches human raters.

```json
{
  "opening": "beta",
  "discovery": "beta",
  "pitch": "beta",
  "objection_handling": "beta",
  "closing": "beta"
}
```

| Status | Behavior in UI |
|---|---|
| `validated` | shown normally (cleared the accuracy bar vs human raters) |
| `beta` | shown **with a caveat tag** (developmental, not evaluative) — current default |
| `hidden` | **not shown at all** (dropped from the breakdown) |

> **Why it matters:** Sarvam's reasoner ranks low on general reasoning (Indic-SOTA, but not a frontier reasoner). Fine-grained numbers stay `beta` and **evidence-backed** until proven — so no one is judged on an unvalidated number. *(See `GOVERNANCE.md` and the delivery report's "trust split".)*

---

## 🧪 Test it yourself — run a recording & check every score against the audio

A developer can take **any call recording**, push it through the live pipeline, and verify the breakdown against what was actually said. This is the whole point of the evidence design: **every score is backed by a quote + timestamp, so you can jump to that moment in the audio and judge if the score is fair.**

### Step 1 — Run a recording through the pipeline

**Option A — API (one recording):**
```bash
# upload → returns { call_id }, processing starts in the background
curl -F "file=@rakesh_call.mp3" -F "name=Rakesh Sharma" -F "phone=98xxxxxxxx" \
     http://localhost:8000/api/calls/upload

# poll until stage = done  (transcribe → analyse → memory)
curl http://localhost:8000/api/calls/<call_id>/processing-status
```

**Option B — script (bulk):** drop files into the `Audio/` folder, then:
```bash
python scripts/import_audio.py          # transcribe + diarize → analyse → memory
```

### Step 2 — Check the transcript FIRST (garbage in → garbage out)
```bash
curl http://localhost:8000/api/calls/<call_id>/transcript
```
Confirm the **2-speaker split is right** (AGENT = the rep, USER = the prospect) before trusting any score. If diarization is wrong, the scores will be too. `transcript_quality` (`ok|low|failed`) flags shaky audio.

### Step 3 — Pull the score breakdown
```bash
curl http://localhost:8000/api/calls/<call_id>/score
```
Each breakdown item looks like this — note the **evidence** block:
```jsonc
{
  "key": "discovery", "label": "Discovery",
  "score": 14, "max": 20,
  "note": "Probed budget and need but never asked about timeline.",
  "evidence": [
    { "turn": 7, "t": "02:35", "speaker": "AGENT", "text": "మీ బడ్జెట్ ఎంత సర్?" }
  ],
  "status": "beta"
}
```

### Step 4 — Validate each score against the recording 🎧
For every dimension, the loop is the same:

```
 score + note  ─┐
                ├─►  open the recording at evidence.t (02:35)
 evidence quote ┘     → listen → does the score match what you hear?
```

| You read | You do | You decide |
|---|---|---|
| `Discovery 14/20` | jump to **02:35** in the audio | did the rep really probe needs well? |
| note: *"never asked about timeline"* | listen around the quoted turn | is the note accurate? |
| evidence quote at turn 7 | confirm the quote is really there | not hallucinated? (turns resolve to real lines) |

Because the score is anchored to a real quote + timestamp, you can **"talk accordingly"** — agree, coach, or dispute it against the actual audio. That's exactly the contestability `GOVERNANCE.md` requires (a rep can challenge any score with the recording).

### ✅ Reviewer sanity checklist
- [ ] Transcript speakers correct (AGENT vs USER not swapped)?
- [ ] Each dimension's **evidence quote actually appears** in the transcript at that timestamp?
- [ ] Does the **note** match the audio at that moment?
- [ ] Is the **0–20 score** defensible given the evidence? (not just "feels off")
- [ ] `Overall` ring == sum of the 5 dimension scores? `Lead Quality` == sum of 4 BANT?
- [ ] Re-run gives a *similar* result? `curl -X POST .../lead-analysis` to re-score.

### ⚡ Shortcut — one command to call & test the scores
Instead of three curls, `scripts/score_one.py` runs the exact scoring pipeline and prints every grade **with its evidence quote + timestamp**:

```bash
python scripts/score_one.py <call_id>          # show the saved breakdown (free, instant)
python scripts/score_one.py <call_id> --rerun  # RE-score with the live model (test prompt changes)
python scripts/score_one.py --text "Turn 1 [AGENT]: hello\nTurn 2 [USER]: what's the price?"
python scripts/score_one.py --prompt           # print the base system prompts
```

Sample output (real call):
```
VERDICT : Hot   (User committed to consultation and gave personal details.)
LEAD QUALITY  (BANT)      = 90/100
   budget     20/25   User accepted price starting ₹55,000.
   need       25/25   Clear need for cosmetic rhinoplasty to improve appearance.
REP EXECUTION (Overall)   = 84/100
   Opening            18/20   Agent introduced procedure, price, and location promptly.
        > [00:03] AGENT: హలో.
   Closing            20/20   Secured firm appointment and personal details.
        > [00:13] AGENT: ఓకే సార్, చెప్పండి సార్.
```

This is the fastest way to **change the base prompt and re-test**: edit `_SCORING_SYS`, then `--rerun` the same call and compare. Use it to A/B a prompt tweak before trusting it.

### Step 5 — Make trust MEASURABLE (the formal gate)
One person eyeballing calls is anecdote. To actually promote a dimension from `beta` → `validated`, run the gold-set harness — it compares AI scores to **human labels** across many calls:

```bash
python scripts/gold_set_eval.py --template   # writes gold_set.json to fill in
#   → label 30–50 real calls (ideally 2 raters each, averaged):
#     [ {"call_id":"call_xxx","labels":{"opening":16,"discovery":14,"pitch":15,
#                                        "objection_handling":12,"closing":10}}, ... ]
python scripts/gold_set_eval.py               # dry-run: per-dimension MAE + correlation report
python scripts/gold_set_eval.py --write       # promote dims that clear the bar → score_dimensions.json
```
**Acceptance bar:** **MAE ≤ 3.0** on the 0–20 scale **and ≥ 8** labeled calls. Anything below the bar stays `beta` (shown with a caveat, never used as a hard verdict).

---

## 8️⃣ Long calls — map-reduce (same rubric)

Calls over **40 turns** are chunked (40-turn windows, 4-turn overlap). Each chunk is **digested** in parallel, then the same scoring prompt scores the **whole call from the digests**.

**Digest system prompt (`_DIGEST_SYS`, verbatim):**
```text
You are summarising ONE part of a longer sales call. Capture what happened, buying signals,
objections, commitments, and any budget/authority/need/timeline/location/product details stated.
```

**Reduce user message (verbatim):**
```text
The call was long; here are ordered segment digests. Score the WHOLE call from them:

--- SEGMENT 1 ---
summary: ...
signals: ...
objections: ...
commitments: ...
budget=... authority=... need=... timeline=... location=... product=...
--- SEGMENT 2 ---
...
```
The reduce step reuses `_SCORING_SYS` (same rubric/schema) → identical breakdown shape.

---

## 9️⃣ Sentiment (separate focused call)

Sentiment is a **separate** call (decomposition = more reliable). **System prompt (`_SENTIMENT_SYS`, verbatim):**
```text
You are a sentiment analyst. For EACH turn shown, output one arc entry: the same turn number,
the role, a sentiment score from -1.0 (very negative) to 1.0 (very positive), and a short label.
Score the PROSPECT's emotion on USER turns and the agent's tone on AGENT turns. Also tag each
turn's intent (introduction|discovery|pitch|objection|buy_signal|defer|close|small_talk|neutral).
```
The per-turn arc → **Sentiment ring** (prospect turns averaged, rescaled to 0–100) and the timeline bar.

---

## 🔧 Model & call settings

| Setting | Value | Why |
|---|---|---|
| Model | `sarvam-105b` (`settings.sarvam_chat_model`) | Indic-SOTA analysis |
| Output mode | **forced tool-calling** (`record_analysis`) | guaranteed valid JSON |
| `reasoning_effort` | `low` | else reasoning model returns empty |
| `max_tokens` | `4000` | fits full structured output |
| Retries | 2 (linear backoff) | absorbs transient 5xx / junk args |

---

## 🛠️ Where to change things

| To change… | Edit |
|---|---|
| The rubric / instructions | `_SCORING_SYS` in `app/utils/lead_analyzer.py` |
| What fields the model returns | `_SCORING_SCHEMA` (same file) |
| Score ranges (0–20 / 0–25) | `clamp(..., 0, 20)` / `clamp(..., 0, 25)` in `_assemble` |
| Show/hide/validate a dimension | `config/score_dimensions.json` |
| Composite-ring weights | `CALL_SCORE_WEIGHTS` in `app/utils/lead_intelligence.py` |
| Long-call chunking | `_CHUNK_TURNS` / `_CHUNK_OVERLAP` |

---

*Every prompt and schema block above is copied verbatim from the codebase — independently verifiable at the cited file paths.*
