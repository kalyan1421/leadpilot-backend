"""
Lead Analyzer — full post-call AI analysis, 2026-standard.

Design (why it's built this way):
  - STRUCTURED OUTPUT via forced tool-calling (app.utils.sarvam.sarvam_extract) — the
    model's output is constrained to a JSON schema, so we never "parse-and-pray". This
    removes the unparseable-JSON failure class entirely.
  - DECOMPOSITION — holistic scoring and per-turn sentiment are SEPARATE focused calls.
    Smaller, single-purpose outputs are far more reliable than one mega-prompt.
  - MAP-REDUCE for long calls — transcripts beyond one window are chunked (with overlap),
    each chunk digested (map), then scored from the digests (reduce). Sentiment is mapped
    per chunk and concatenated. Short calls collapse to the single-window fast path.
  - DETERMINISTIC AGGREGATION stays downstream (lead_intelligence): the LLM emits signals,
    code computes BANT totals / agent total / rings. Provider = Sarvam only (key rotation
    handled in app.utils.sarvam).

Output contract is unchanged — every field the DB / /score endpoint / memory read is produced:
  sentiment_arc, intent_tags, entities, bant_score, bant_breakdown, lead_verdict,
  call_summary, key_points, next_steps(+action_label), next_action, agent_debrief(+*_note).
"""

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.config import settings
from app.utils.sarvam import sarvam_extract

_MAP_WORKERS = 4  # bounded concurrency for per-chunk map calls on long transcripts

logger = logging.getLogger(__name__)

# Retry each structured call a couple times — Sarvam can transiently 5xx / return junk args.
_MAX_RETRIES = 2
_RETRY_BASE = 2.0

# Map-reduce knobs: calls under one window take the fast path; longer calls are chunked.
_CHUNK_TURNS = 40
_CHUNK_OVERLAP = 4

_VERDICTS = ["Hot", "Warm", "Cold", "Junk"]
_TONES = ["positive", "neutral", "negative", "mixed"]
_ACTION_TYPES = ["send_whatsapp", "send_sms", "send_email", "schedule_callback", "schedule_visit", "note"]

_ACTION_LABELS = {
    "send_whatsapp": "Send now", "send_sms": "Send now", "send_email": "Send now",
    "schedule_callback": "Schedule", "schedule_visit": "Schedule", "note": "Note",
}

# ---------------------------------------------------------------------------
# Tool schemas (forced — output is constrained to these)
# ---------------------------------------------------------------------------

_SCORING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        # reasoning-bearing verdict first so the model commits after weighing the call
        "lead_verdict": {"type": "string", "enum": _VERDICTS},
        "lead_verdict_reason": {"type": "string"},
        # BANT — each dimension 0-25 + a short reason
        "budget_score": {"type": "integer"}, "budget_reason": {"type": "string"},
        "authority_score": {"type": "integer"}, "authority_reason": {"type": "string"},
        "need_score": {"type": "integer"}, "need_reason": {"type": "string"},
        "timeline_score": {"type": "integer"}, "timeline_reason": {"type": "string"},
        # Telecaller execution — each dimension 0-20 + a one-line note (renders under the bar)
        "opening_score": {"type": "integer"}, "opening_note": {"type": "string"},
        "discovery_score": {"type": "integer"}, "discovery_note": {"type": "string"},
        "pitch_score": {"type": "integer"}, "pitch_note": {"type": "string"},
        "objection_handling_score": {"type": "integer"}, "objection_handling_note": {"type": "string"},
        "closing_score": {"type": "integer"}, "closing_note": {"type": "string"},
        # Punctuality (0-10) — judged from the transcript's OWN turn timestamps
        # (pacing: no rambling/dead air, timely progression), not any external
        # scheduling data (the system has no reliable call-was-scheduled-for-X
        # signal to compare against). Additive to the 5 dimensions above, not a
        # replacement — those keep their existing 0-20 scale unchanged.
        "punctuality_score": {"type": "integer"}, "punctuality_note": {"type": "string"},
        "punctuality_evidence_turns": {"type": "array", "items": {"type": "integer"}},
        # Evidence: the transcript Turn numbers that justify each dimension's score.
        # Resolved to exact quote+timestamp+speaker downstream (auditable, not paraphrased).
        "opening_evidence_turns": {"type": "array", "items": {"type": "integer"}},
        "discovery_evidence_turns": {"type": "array", "items": {"type": "integer"}},
        "pitch_evidence_turns": {"type": "array", "items": {"type": "integer"}},
        "objection_handling_evidence_turns": {"type": "array", "items": {"type": "integer"}},
        "closing_evidence_turns": {"type": "array", "items": {"type": "integer"}},
        # Script compliance checklist — order/timing judgment for the SAME 5
        # dimensions above, reused as "steps" rather than a separate per-org
        # custom script definition (none exists in this system).
        "script_compliance": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "step": {"type": "string", "enum": [
                    "opening", "discovery", "pitch", "objection_handling", "closing",
                ]},
                "status": {"type": "string", "enum": ["followed", "too_early", "too_late", "skipped"]},
                "note": {"type": "string"},
            },
            "required": ["step", "status"],
        }},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
        # Summary + actions
        "headline": {"type": "string"},
        "key_moments": {"type": "array", "items": {"type": "string"}},
        "objections_raised": {"type": "array", "items": {"type": "string"}},
        "commitments_made": {"type": "array", "items": {"type": "string"}},
        "overall_tone": {"type": "string", "enum": _TONES},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "step": {"type": "integer"},
                "text": {"type": "string"},
                "action_type": {"type": "string", "enum": _ACTION_TYPES},
            },
            "required": ["text", "action_type"],
        }},
        "recommended_action": {"type": "string"},
        "channel": {"type": "string"},
        "urgency": {"type": "string"},
        # Entities (optional — left null when not stated, to avoid hallucination)
        "entity_budget": {"type": "string"}, "entity_authority": {"type": "string"},
        "entity_need": {"type": "string"}, "entity_timeline": {"type": "string"},
        "entity_location": {"type": "string"}, "entity_product_interest": {"type": "string"},
        "objections": {"type": "array", "items": {"type": "string"}},
        # Relevance filter — only meaningful when ORGANISATION CONTEXT was supplied.
        # Lets a call about a wrong number / unrelated topic be flagged instead of
        # scored as if it were a normal (bad) sales conversation.
        "is_relevant": {"type": "boolean"},
        "relevance_reason": {"type": "string"},
    },
    "required": [
        "lead_verdict", "budget_score", "authority_score", "need_score", "timeline_score",
        "opening_score", "discovery_score", "pitch_score", "objection_handling_score",
        "closing_score", "punctuality_score", "script_compliance",
        "key_points", "next_steps", "overall_tone", "is_relevant",
    ],
}

_SENTIMENT_LABELS = ["frustrated", "cautious", "neutral", "interested"]
_INTENT_TAGS = [
    "introduction", "discovery", "pitch", "objection", "buy_signal",
    "defer", "close", "small_talk", "neutral",
]

_SENTIMENT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "arc": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "turn": {"type": "integer"},
                "role": {"type": "string"},
                "score": {"type": "number"},
                # Enum-constrained (was free-text): an unconstrained label let
                # the model mash sentiment + intent into one verbose string
                # (e.g. "greeting (small_talk)"), which on longer calls
                # inflated the response past max_tokens and truncated the
                # JSON mid-object — the whole sentiment_arc then came back
                # empty even though scoring/BANT succeeded. Matches the same
                # 4-value vocabulary sentiment_timeline() already computes
                # deterministically (lead_intelligence.sentiment_label).
                "label": {"type": "string", "enum": _SENTIMENT_LABELS},
            },
            "required": ["turn", "score"],
        }},
        "intent": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "turn": {"type": "integer"},
                "role": {"type": "string"},
                "intent": {"type": "string", "enum": _INTENT_TAGS},
            },
            "required": ["turn", "intent"],
        }},
    },
    "required": ["arc"],
}

_DIGEST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
        "objections": {"type": "array", "items": {"type": "string"}},
        "commitments": {"type": "array", "items": {"type": "string"}},
        "budget": {"type": "string"}, "authority": {"type": "string"},
        "need": {"type": "string"}, "timeline": {"type": "string"},
        "location": {"type": "string"}, "product_interest": {"type": "string"},
    },
    "required": ["summary"],
}

_SCORING_SYS = (
    "You are an expert sales-call analyst for an Indian telecalling team. Analyse the call "
    "and record the structured result. Rules: BANT dimensions are each 0-25; the 5 telecaller "
    "dimensions (opening/discovery/pitch/objection_handling/closing) are each 0-20; each *_note "
    "is ONE short sentence (max 14 words) explaining that score; verdict: Hot (strong buy intent), "
    "Warm (interested), Cold (weak), Junk (wrong number/irrelevant). Leave an entity blank only if "
    "truly not stated. next_steps: 1-4 concrete actions with a valid action_type. "
    "CRITICAL: for each of the 5 telecaller dimensions, return *_evidence_turns = the 1-2 Turn "
    "NUMBERS from the transcript that most justify that score. Use DISTINCT, specific turns where "
    "that behaviour actually happens (e.g. the closing turns for closing, the pitch turns for pitch) "
    "— do NOT default to Turn 1 for every dimension. Cite the actual turns you judged from. "
    "PUNCTUALITY (0-10): judge PACING from the transcript's own turn timestamps only — good "
    "punctuality means no unnecessary long silences/rambling and timely progression through the "
    "call's stages; this is NOT about whether the call happened at some externally scheduled time "
    "(you have no visibility into that). "
    "SCRIPT COMPLIANCE: for each of the 5 telecaller dimensions (as 'step'), judge whether the "
    "telecaller followed it at the right point in the call: 'followed' (done, right timing), "
    "'too_early' (done, but prematurely — e.g. pricing before discovery), 'too_late' (done, but "
    "delayed), or 'skipped' (never done). One entry per step, every call. "
    "RELEVANCE FILTER: if an ORGANISATION CONTEXT block is present, first judge whether this call "
    "is actually about that business's stated services. If it clearly is not (wrong number, "
    "unrelated topic, spam), set is_relevant=false and explain why in relevance_reason — this is "
    "a LABEL only. Still score EVERY dimension normally on its own merits from what actually "
    "happened in the call (telecaller performance, BANT/lead quality, sentiment, punctuality, and "
    "every script_compliance entry), exactly as you would for a relevant call. Do NOT force scores "
    "to 0 or the verdict to Junk just because the call is off-topic — judge honestly: a genuine "
    "wrong number will naturally score low on lead quality, but let the transcript decide rather "
    "than blanking it. If it IS relevant, set is_relevant=true and let the organisation's "
    "services/audience/brand voice sharpen your judgement of fit and next steps. If no "
    "ORGANISATION CONTEXT is present, always set is_relevant=true and score normally."
)
_SENTIMENT_SYS = (
    "You are a sentiment analyst. For EACH turn shown, output one arc entry: the same turn number, "
    "the role, a sentiment score from -1.0 (very negative) to 1.0 (very positive), and label = EXACTLY "
    "ONE of frustrated|cautious|neutral|interested (never combine it with the intent tag below). "
    "Score the PROSPECT's emotion on USER turns and the agent's tone on AGENT turns. Separately, tag "
    "each turn's intent as EXACTLY ONE of "
    "introduction|discovery|pitch|objection|buy_signal|defer|close|small_talk|neutral."
)
_DIGEST_SYS = (
    "You are summarising ONE part of a longer sales call. Capture what happened, buying signals, "
    "objections, commitments, and any budget/authority/need/timeline/location/product details stated."
)

class LeadAnalyzer:
    def __init__(self):
        # Reasoning provider is config-selected. STT/diarization stay on Sarvam regardless;
        # this only decides which LLM does the structured analysis/scoring/sentiment.
        self.provider = (settings.reasoning_provider or "sarvam").lower()
        self.model = settings.gemini_model if self.provider == "gemini" else settings.sarvam_chat_model

    # -- public ------------------------------------------------------------

    def analyze(self, transcript: Dict[str, Any], org_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        turns = self._turns(transcript)
        if not turns:
            return self._empty_result()
        try:
            chunks = _chunk(turns, _CHUNK_TURNS, _CHUNK_OVERLAP)

            def _score() -> Dict[str, Any]:
                if len(chunks) == 1:
                    return self._call(self._score_messages(self._numbered(turns), org_context), _SCORING_SCHEMA, "record_analysis")
                logger.info(f"Lead analysis: long call → map-reduce over {len(chunks)} chunks")
                def _digest(c):
                    try:
                        return self._call(self._digest_messages(self._numbered(c["turns"], c["start"])),
                                          _DIGEST_SCHEMA, "record_digest")
                    except Exception as e:  # one bad chunk shouldn't kill the whole call
                        logger.warning(f"digest chunk @ {c['start']} failed: {str(e)[:80]}")
                        return {}
                with ThreadPoolExecutor(max_workers=_MAP_WORKERS) as ex:
                    digests = list(ex.map(_digest, chunks))
                return self._call(self._reduce_messages(digests, org_context), _SCORING_SCHEMA, "record_analysis")

            # Sentiment is computed straight from `chunks` — it doesn't depend on
            # scoring/digests at all, so run it concurrently with _score() instead
            # of after it. Was previously sequential (scoring fully done, *then*
            # sentiment started), wasting the entire sentiment phase's wall-clock
            # time on every call.
            with ThreadPoolExecutor(max_workers=2) as top_ex:
                score_future = top_ex.submit(_score)
                sentiment_future = top_ex.submit(self._sentiment, chunks)
                scoring = score_future.result()
                arc, intent = sentiment_future.result()

            result = self._assemble(scoring, arc, intent, turns)
            logger.info(f"Lead analysis ok: verdict={result['lead_verdict']} bant={result['bant_score']} "
                        f"agent={result['agent_debrief']['total_score']} arc={len(arc)}")
            return result
        except Exception as e:
            logger.error(f"Lead analysis failed: {e}", exc_info=True)
            return None

    # -- LLM calls (with light retry) --------------------------------------

    def _call(self, messages: List[Dict[str, str]], schema: Dict[str, Any], tool: str,
              max_tokens: int = 4000) -> Dict[str, Any]:
        last = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if self.provider == "gemini":
                    from app.utils.gemini import gemini_extract
                    return gemini_extract(messages, schema=schema, tool_name=tool,
                                          model=self.model, max_tokens=max_tokens)
                return sarvam_extract(messages, schema=schema, tool_name=tool,
                                      model=self.model, max_tokens=max_tokens)
            except Exception as e:
                last = e
                logger.warning(f"{tool} attempt {attempt + 1} failed: {str(e)[:120]}")
                time.sleep(_RETRY_BASE * (attempt + 1))
        raise RuntimeError(f"{tool} failed after retries: {last}")

    def _sentiment(self, chunks: List[Dict[str, Any]]):
        """Per-turn sentiment + intent, mapped per chunk (in parallel) and concatenated."""
        def one(c):
            msgs = [{"role": "system", "content": _SENTIMENT_SYS},
                    {"role": "user", "content": "TRANSCRIPT:\n" + self._numbered(c["turns"], c["start"])}]
            # Two array entries (arc + intent) per turn, now enum-constrained
            # (cheap tokens) rather than free-text — but still scale with
            # chunk size so a long chunk's response can't get truncated
            # mid-JSON the way an unconstrained label previously could at a
            # flat 4000-token budget (see _SENTIMENT_SCHEMA's label enum).
            budget = max(4000, len(c["turns"]) * 120)
            try:
                return self._call(msgs, _SENTIMENT_SCHEMA, "record_sentiment", max_tokens=budget)
            except Exception as e:
                logger.warning(f"sentiment chunk @ {c['start']} failed: {str(e)[:80]}")
                return None

        with ThreadPoolExecutor(max_workers=_MAP_WORKERS) as ex:
            outs = list(ex.map(one, chunks))

        arc: List[Dict[str, Any]] = []
        intent: List[Dict[str, Any]] = []
        seen = set()
        for out in outs:
            if not out:
                continue
            for item in (out.get("arc") or []):
                t = item.get("turn")
                if type(t) is int and t not in seen:  # overlap dedup; exclude bool
                    seen.add(t)
                    arc.append(item)
            intent.extend(out.get("intent") or [])
        arc.sort(key=lambda x: x.get("turn", 0))
        return arc, intent

    # -- message builders --------------------------------------------------

    @staticmethod
    def _org_context_block(org_context: Optional[Dict[str, Any]]) -> str:
        """Renders the Organisation Knowledge Base as prompt context so scoring,
        relevance-filtering, and next-action suggestions are grounded in what this
        specific business actually sells — not generic sales-call heuristics."""
        if not org_context:
            return ""
        services = ", ".join(org_context.get("services") or []) or "not specified"
        usps = ", ".join(org_context.get("usps") or []) or "not specified"
        competitors = ", ".join(org_context.get("competitors") or []) or "not specified"
        languages = ", ".join(org_context.get("languages") or []) or "not specified"
        pricing_min, pricing_max = org_context.get("pricing_min"), org_context.get("pricing_max")
        if pricing_min is not None or pricing_max is not None:
            pricing = f"{pricing_min or '?'} - {pricing_max or '?'}"
        else:
            pricing = "not specified"
        return (
            "\n\nORGANISATION CONTEXT:\n"
            f"Business: {org_context.get('name') or 'unknown'} ({org_context.get('industry') or 'unspecified industry'})\n"
            f"Website: {org_context.get('website_url') or 'not specified'}\n"
            f"Services offered: {services}\n"
            f"Pricing range: {pricing}\n"
            f"Target audience: {org_context.get('target_audience') or 'not specified'}\n"
            f"Brand voice: {org_context.get('brand_voice') or 'not specified'}\n"
            f"Competitors: {competitors}\n"
            f"Languages spoken: {languages}\n"
            f"USPs: {usps}\n"
        )

    def _score_messages(self, text: str, org_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        return [{"role": "system", "content": _SCORING_SYS + self._org_context_block(org_context)},
                {"role": "user", "content": f"TRANSCRIPT:\n{text}"}]

    def _digest_messages(self, text: str) -> List[Dict[str, str]]:
        return [{"role": "system", "content": _DIGEST_SYS},
                {"role": "user", "content": f"CALL SEGMENT:\n{text}"}]

    def _reduce_messages(self, digests: List[Dict[str, Any]], org_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        digests = [d if isinstance(d, dict) else {} for d in digests]  # tolerate bad map output
        block = "\n\n".join(
            f"--- SEGMENT {i + 1} ---\nsummary: {d.get('summary')}\n"
            f"signals: {', '.join(d.get('signals') or [])}\n"
            f"objections: {', '.join(d.get('objections') or [])}\n"
            f"commitments: {', '.join(d.get('commitments') or [])}\n"
            f"budget={d.get('budget')} authority={d.get('authority')} need={d.get('need')} "
            f"timeline={d.get('timeline')} location={d.get('location')} product={d.get('product_interest')}"
            for i, d in enumerate(digests)
        )
        return [{"role": "system", "content": _SCORING_SYS + self._org_context_block(org_context)},
                {"role": "user", "content": "The call was long; here are ordered segment digests. "
                                            f"Score the WHOLE call from them:\n\n{block}"}]

    # -- assembly into the output contract ---------------------------------

    def _assemble(self, s: Dict[str, Any], arc: List[Dict[str, Any]], intent: List[Dict[str, Any]],
                  turns: List[Dict[str, Any]]) -> Dict[str, Any]:
        def clamp(v, lo, hi):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return lo
            if not math.isfinite(f):   # guard inf/-inf/nan (OverflowError on int(round(inf)))
                return lo
            return max(lo, min(hi, int(round(f))))

        bant = {
            "budget": {"score": clamp(s.get("budget_score"), 0, 25), "reason": s.get("budget_reason") or ""},
            "authority": {"score": clamp(s.get("authority_score"), 0, 25), "reason": s.get("authority_reason") or ""},
            "need": {"score": clamp(s.get("need_score"), 0, 25), "reason": s.get("need_reason") or ""},
            "timeline": {"score": clamp(s.get("timeline_score"), 0, 25), "reason": s.get("timeline_reason") or ""},
        }
        bant_score = sum(d["score"] for d in bant.values())  # SUM (0-100), not average

        debrief = {
            "strengths": s.get("strengths") or [],
            "improvements": s.get("improvements") or [],
            "opening_score": clamp(s.get("opening_score"), 0, 20), "opening_note": s.get("opening_note") or "",
            "opening_evidence": self._evidence(s.get("opening_evidence_turns"), turns),
            "discovery_score": clamp(s.get("discovery_score"), 0, 20), "discovery_note": s.get("discovery_note") or "",
            "discovery_evidence": self._evidence(s.get("discovery_evidence_turns"), turns),
            "pitch_score": clamp(s.get("pitch_score"), 0, 20), "pitch_note": s.get("pitch_note") or "",
            "pitch_evidence": self._evidence(s.get("pitch_evidence_turns"), turns),
            "objection_handling_score": clamp(s.get("objection_handling_score"), 0, 20),
            "objection_handling_note": s.get("objection_handling_note") or "",
            "objection_handling_evidence": self._evidence(s.get("objection_handling_evidence_turns"), turns),
            "closing_score": clamp(s.get("closing_score"), 0, 20), "closing_note": s.get("closing_note") or "",
            "closing_evidence": self._evidence(s.get("closing_evidence_turns"), turns),
            "punctuality_score": clamp(s.get("punctuality_score"), 0, 10),
            "punctuality_note": s.get("punctuality_note") or "",
            "punctuality_evidence": self._evidence(s.get("punctuality_evidence_turns"), turns),
            "script_compliance": self._script_compliance(s.get("script_compliance")),
        }
        # total_score is unchanged (still the original 5 dims, /100) — punctuality is
        # additive and surfaced separately (get_call_score's breakdown, dashboard.py's
        # quality aggregate) rather than folded into what "Overall"/total_score already
        # means everywhere it's displayed today.
        debrief["total_score"] = (debrief["opening_score"] + debrief["discovery_score"] + debrief["pitch_score"]
                                  + debrief["objection_handling_score"] + debrief["closing_score"])

        next_steps = []
        for i, ns in enumerate(s.get("next_steps") or [], 1):
            if not isinstance(ns, dict) or not ns.get("text"):
                continue
            at = ns.get("action_type") if ns.get("action_type") in _ACTION_TYPES else "note"
            next_steps.append({"step": len(next_steps) + 1, "text": ns["text"],
                               "action_type": at, "action_label": _ACTION_LABELS.get(at, "Note")})

        verdict = s.get("lead_verdict") if s.get("lead_verdict") in _VERDICTS else "Cold"

        return {
            "sentiment_arc": [self._norm_arc(a) for a in arc],
            "intent_tags": intent,
            "entities": {
                "budget": s.get("entity_budget") or None, "authority": s.get("entity_authority") or None,
                "need": s.get("entity_need") or None, "timeline": s.get("entity_timeline") or None,
                "location": s.get("entity_location") or None, "product_interest": s.get("entity_product_interest") or None,
                "objections": s.get("objections") or [],
            },
            "bant_breakdown": bant,
            "bant_score": bant_score,
            "lead_verdict": verdict,
            "lead_verdict_reason": s.get("lead_verdict_reason") or "",
            "call_summary": {
                "headline": s.get("headline") or "", "key_moments": s.get("key_moments") or [],
                "objections_raised": s.get("objections_raised") or [],
                "commitments_made": s.get("commitments_made") or [],
                "overall_tone": s.get("overall_tone") if s.get("overall_tone") in _TONES else "neutral",
            },
            "key_points": s.get("key_points") or [],
            "next_steps": next_steps,
            "next_action": {
                "recommended_action": s.get("recommended_action") or ("call_back" if verdict in ("Hot", "Warm") else "nurture"),
                "follow_up_script": "",
                "channel": s.get("channel") or "whatsapp",
                "urgency": s.get("urgency") or ("immediate" if verdict == "Hot" else "within_week"),
            },
            "agent_debrief": debrief,
            "is_relevant": bool(s.get("is_relevant", True)),
            "relevance_reason": s.get("relevance_reason") or "",
        }

    @staticmethod
    def _norm_arc(a: Dict[str, Any]) -> Dict[str, Any]:
        try:
            score = max(-1.0, min(1.0, float(a.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        return {"turn": a.get("turn"), "role": (a.get("role") or "").upper() or "USER",
                "score": score, "label": a.get("label") or ("positive" if score > 0.1 else "negative" if score < -0.1 else "neutral")}

    @staticmethod
    def _evidence(turn_nums: Any, turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Resolve cited Turn numbers → exact {turn, t, speaker, text} from the transcript (auditable quote)."""
        out: List[Dict[str, Any]] = []
        seen = set()
        for n in (turn_nums or []):
            if type(n) is int and 1 <= n <= len(turns) and n not in seen:  # exclude bool, dedup
                seen.add(n)
                t = turns[n - 1]
                text = (t.get("content") or "").strip()
                if text:
                    out.append({"turn": n, "t": t.get("timestamp") or "0:00",
                                "speaker": (t.get("role") or "").upper() or "USER", "text": text})
                if len(out) >= 3:
                    break
        return out

    _SCRIPT_STEPS = ("opening", "discovery", "pitch", "objection_handling", "closing")
    _SCRIPT_STATUSES = ("followed", "too_early", "too_late", "skipped")

    @classmethod
    def _script_compliance(cls, raw: Any) -> List[Dict[str, str]]:
        """Validates/dedupes the model's script_compliance array — one entry per
        known step, unknown steps/statuses dropped rather than trusted verbatim."""
        by_step: Dict[str, Dict[str, str]] = {}
        for entry in (raw or []):
            if not isinstance(entry, dict):
                continue
            step = entry.get("step")
            status = entry.get("status")
            if step not in cls._SCRIPT_STEPS or status not in cls._SCRIPT_STATUSES:
                continue
            by_step[step] = {"step": step, "status": status, "note": entry.get("note") or ""}
        # Any step the model didn't return an entry for defaults to "skipped" —
        # better than silently omitting it from the checklist.
        return [by_step.get(step, {"step": step, "status": "skipped", "note": ""})
                for step in cls._SCRIPT_STEPS]

    # -- transcript helpers ------------------------------------------------

    @staticmethod
    def _turns(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(transcript, dict):
            return []
        turns = transcript.get("turns") or []
        return [t for t in turns if isinstance(t, dict) and (t.get("content") or "").strip()]

    @staticmethod
    def _numbered(turns: List[Dict[str, Any]], start: int = 1) -> str:
        return "\n".join(
            f"Turn {start + i} [{(t.get('role') or 'UNKNOWN').upper()}]: {t.get('content', '').strip()}"
            for i, t in enumerate(turns)
        )

    @classmethod
    def _empty_debrief(cls, reason: str = "") -> Dict[str, Any]:
        """A fully-shaped debrief with every field the success path emits, all
        zeroed. Used for the no-transcript path AND the analysis-failure path
        (see empty_analysis) so the Score tab always has all 6 dimensions +
        script_compliance to render — greyed at 0 — instead of a blank tab or
        missing keys."""
        debrief: Dict[str, Any] = {"strengths": [], "improvements": []}
        for dim in ("opening", "discovery", "pitch", "objection_handling", "closing"):
            debrief[f"{dim}_score"] = 0
            debrief[f"{dim}_note"] = reason
            debrief[f"{dim}_evidence"] = []
        debrief["punctuality_score"] = 0
        debrief["punctuality_note"] = reason
        debrief["punctuality_evidence"] = []
        debrief["script_compliance"] = [
            {"step": step, "status": "skipped", "note": ""} for step in cls._SCRIPT_STEPS
        ]
        debrief["total_score"] = 0
        return debrief

    @classmethod
    def _empty_result(cls, reason: str = "No transcript available") -> Dict[str, Any]:
        return {
            "sentiment_arc": [], "intent_tags": [],
            "entities": {"budget": None, "authority": None, "need": None, "timeline": None,
                         "location": None, "product_interest": None, "objections": []},
            "bant_breakdown": {k: {"score": 0, "reason": "no transcript"} for k in ("budget", "authority", "need", "timeline")},
            "bant_score": 0, "lead_verdict": "Junk", "lead_verdict_reason": reason,
            "call_summary": {"headline": "No transcript", "key_moments": [], "objections_raised": [],
                             "commitments_made": [], "overall_tone": "neutral"},
            "key_points": [], "next_steps": [],
            "next_action": {"recommended_action": "disqualify", "follow_up_script": "", "channel": "none", "urgency": "low_priority"},
            "agent_debrief": cls._empty_debrief(reason),
            "is_relevant": True, "relevance_reason": "",
        }


def _chunk(turns: List[Dict[str, Any]], size: int, overlap: int) -> List[Dict[str, Any]]:
    """Split turns into overlapping windows. Returns [{start: 1-based global index, turns: [...]}]."""
    if len(turns) <= size:
        return [{"start": 1, "turns": turns}]
    out, i = [], 0
    step = max(1, size - overlap)
    while i < len(turns):
        out.append({"start": i + 1, "turns": turns[i:i + size]})
        i += step
    return out


_analyzer: Optional[LeadAnalyzer] = None


def get_analyzer() -> LeadAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = LeadAnalyzer()
    return _analyzer


def analyze_call(transcript: Dict[str, Any], org_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    return get_analyzer().analyze(transcript, org_context=org_context)


def empty_analysis(reason: str = "Analysis unavailable") -> Dict[str, Any]:
    """Fully-shaped, all-zero analysis result. The upload pipeline persists this
    (with LeadAnalysis.status='failed') when the analyzer can't produce a real
    result, so the Score tab still renders all 6 dimensions greyed at 0 with an
    error banner instead of 404-ing or showing a blank screen."""
    return LeadAnalyzer._empty_result(reason)
